using System;
using System.Collections.Generic;
using System.IO;
using System.Threading;
using System.Threading.Tasks;
using Newtonsoft.Json.Linq;
using VideoOS.Platform;
using VideoOS.Platform.Background;
using VideoOS.Platform.Messaging;

namespace MilestoneSearch
{
    public sealed class CollectorPlugin:BackgroundPlugin
    {
        private readonly List<object> receivers=new List<object>();
        private volatile PluginSettings settings;
        private Outbox outbox;
        private CancellationTokenSource stop;
        private Task sender;
        private readonly VideoOS.Platform.Proxy.AlarmClient.AlarmClientManager alarms=new VideoOS.Platform.Proxy.AlarmClient.AlarmClientManager();
        public override Guid Id=>Ids.Background;
        public override string Name=>"Milestone Search Collector";
        public override List<EnvironmentType> TargetEnvironments=>new List<EnvironmentType>{EnvironmentType.Service};
        public override void Init()
        {
            PluginLog.Info("Collector Init entered; assembly="+typeof(CollectorPlugin).Assembly.FullName);
            stop=new CancellationTokenSource();
            string folder=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData),"MilestoneSearch","outbox");
            outbox=new Outbox(folder,256*1024*1024);
            receivers.Add(EnvironmentManager.Instance.RegisterReceiver(ConfigurationChanged,new MessageIdFilter(MessageId.Server.ConfigurationChangedIndication)));
            receivers.Add(EnvironmentManager.Instance.RegisterReceiver(Notification,new MessageIdFilter(MessageId.Server.NewEventIndication)));
            receivers.Add(EnvironmentManager.Instance.RegisterReceiver(Notification,new MessageIdFilter(MessageId.Server.NewAlarmIndication)));
            receivers.Add(EnvironmentManager.Instance.RegisterReceiver(AlarmChanged,new MessageIdFilter(MessageId.Server.ChangedAlarmIndication)));
            ReloadSettings("startup");
            sender=Task.Run(()=>SendLoop(stop.Token));
        }
        private object ConfigurationChanged(Message message,FQID sender,FQID related)
        {
            ReloadSettings("configuration_changed"); return null;
        }
        private void ReloadSettings(string reason)
        {
            try
            {
                var next=PluginSettings.Load();next.Validate();outbox.SetQuota(next.OutboxBytes);settings=next;
                PluginLog.Info("Collector settings loaded: site="+next.SiteId+", enabled="+next.Enabled+", backend="+next.BackendUrl+", reason="+reason);
                if(next.Enabled)outbox.Enqueue(new JObject { ["route"]="collector/reconcile",["data"]=new JObject {["site_id"]=next.SiteId,["reason"]=reason}}.ToString());
            }
            catch(Exception ex){Log(ex);}
        }
        private object Notification(Message message,FQID sender,FQID related)
        {
            var current=settings;
            if(current==null || !current.Enabled) return null;
            try {var envelope=EnvelopeMapper.Map(message.Data,current.SiteId);outbox.Enqueue(new JObject {["route"]="ingest",["data"]=envelope}.ToString());PluginLog.Info("Notification queued: kind="+(string)envelope["kind"]+", source_guid="+(string)envelope["source_guid"]+", event_type="+(string)envelope["event_type"]);}
            catch(Exception ex){Log(ex);} return null;
        }
        private object AlarmChanged(Message message,FQID sender,FQID related)
        {
            var current=settings;
            if(current==null || !current.Enabled) return null;
            try
            {
                var change=message.Data as ChangedAlarmData;
                if(change==null)return null;
                Guid alarmId=change.AlarmId;
                DateTime changedAt=DateTime.UtcNow;
                // Read the current alarm through the Event Server's own session; no REST account is needed.
                Task.Run(()=>{
                    try
                    {
                        var alarm=alarms.GetAlarmClient(EnvironmentManager.Instance.MasterSite.ServerId).Get(alarmId);
                        outbox.Enqueue(new JObject {["route"]="ingest",["data"]=EnvelopeMapper.Map(alarm,current.SiteId,changedAt)}.ToString());
                    }
                    catch(Exception ex)
                    {
                        Log(ex);
                        outbox.Enqueue(new JObject {["route"]="collector/reconcile",["data"]=new JObject {
                            ["site_id"]=current.SiteId,["reason"]="alarm_changed",["alarm_id"]=alarmId.ToString()}}.ToString());
                    }
                });
            }
            catch(Exception ex){Log(ex);} return null;
        }
        private async Task SendLoop(CancellationToken cancellation)
        {
            while(!cancellation.IsCancellationRequested)
            {
                try
                {
                    var current=settings;
                    string secret=CollectorToken();
                    if(current?.Enabled==true && !string.IsNullOrWhiteSpace(secret))
                    using(var client=new BackendClient(current.BackendUrl,secret))
                        await outbox.DrainOnce(async body=>{if(cancellation.IsCancellationRequested)return DeliveryResult.Retry;var data=JObject.Parse(body);return await client.Deliver((string)data["route"],data["data"]);});
                    else if(current?.Enabled==true) throw new InvalidOperationException("Collector token not found: set MILESTONE_SEARCH_COLLECTOR_TOKEN or install the backend on this machine.");
                }
                catch(Exception ex){Log(ex);}
                try{await Task.Delay(2000,cancellation);}catch(OperationCanceledException){break;}
            }
        }
        // The backend service on this machine writes its collector token here; the variable overrides it.
        private static string CollectorToken()
        {
            string value=Environment.GetEnvironmentVariable("MILESTONE_SEARCH_COLLECTOR_TOKEN");
            if(!string.IsNullOrWhiteSpace(value))return value.Trim();
            string file=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData),"MilestoneSearch","collector.token");
            try{return File.Exists(file)?File.ReadAllText(file).Trim():null;}catch(Exception ex){Log(ex);return null;}
        }
        private static void Log(Exception ex){PluginLog.Error(ex);}
        public override void Close()
        {
            foreach(var receiver in receivers) EnvironmentManager.Instance.UnRegisterReceiver(receiver);
            receivers.Clear(); stop?.Cancel();
            if(sender!=null && !sender.Wait(TimeSpan.FromSeconds(35))) PluginLog.Error(new TimeoutException("Sender shutdown timed out; unacknowledged files remain in outbox."));
            stop?.Dispose();
        }
    }
}
