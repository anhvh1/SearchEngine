using System;
using System.IO;
using System.Net;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Text;
using System.Threading.Tasks;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using VideoOS.Platform;
using VideoOS.Platform.Util;

namespace MilestoneSearch
{
    public enum DeliveryResult { Delivered, Retry, Reject }
    public static class Ids
    {
        public static readonly Guid ManagementPlugin=new Guid("ce1fcaf9-632e-4650-a9cf-e4d3225f395d");
        public static readonly Guid EventServerPlugin=new Guid("c4a64fd0-8037-442d-943a-10da2e75d9e6");
        public static readonly Guid SmartClientPlugin=new Guid("07b2576d-7b28-4556-98c4-6444d94262d8");
        public static readonly Guid Kind=new Guid("5f9e7faa-58df-4fbd-8c70-baf645ed4b2f");
        public static readonly Guid Configuration=new Guid("a8bb91f5-ae78-4080-b1c6-559d29793cf5");
        public static readonly Guid Background=new Guid("f5fb99bc-e288-4ae7-8af1-0bc7425e70a4");
        public static readonly Guid Workspace=new Guid("ab709ea5-9e07-419a-84b3-50e9af6b95b8");
        public static readonly Guid View=new Guid("43d608f2-24ed-49ca-9e47-54c7e10a8227");
    }
    public sealed class PluginSettings
    {
        public string BackendUrl {get;set;}="http://127.0.0.1:8765";
        // Defaults work when the backend runs on the Event Server machine: nothing to configure.
        public string SiteId {get;set;}="main";
        public bool Enabled {get;set;}=true;
        public long OutboxBytes {get;set;}=256*1024*1024;
        public static Item Item()
        {
            return VideoOS.Platform.Configuration.Instance.GetItemConfiguration(Ids.ManagementPlugin,Ids.Kind,Ids.Configuration)
                ?? new Item(new FQID(VideoOS.Platform.Configuration.Instance.ServerFQID.ServerId,Guid.Empty,Ids.Configuration,FolderType.No,Ids.Kind),"Milestone Search");
        }
        public static PluginSettings Load()
        {
            var item=Item();
            return item.Properties.ContainsKey("SearchSettings")?JsonConvert.DeserializeObject<PluginSettings>(item.Properties["SearchSettings"]):new PluginSettings();
        }
        public void Validate()
        {
            if(!Uri.TryCreate(BackendUrl,UriKind.Absolute,out var uri) || (uri.Scheme!="https" && !(uri.Scheme=="http"&&IsPrivateNetwork(uri))))
                throw new ArgumentException("Backend URL requires HTTPS, except loopback or private LAN IP address.");
            if(string.IsNullOrWhiteSpace(SiteId) || SiteId.Length>128 || OutboxBytes<1024*1024) throw new ArgumentException("Invalid site or outbox quota.");
        }
        // Plain HTTP is accepted only for IP literals on loopback, RFC 1918, link-local or IPv6 ULA networks.
        public static bool IsPrivateNetwork(Uri uri)
        {
            if(uri.IsLoopback)return true;
            if(!IPAddress.TryParse(uri.DnsSafeHost,out var ip))return false;
            byte[] b=ip.GetAddressBytes();
            if(ip.AddressFamily==System.Net.Sockets.AddressFamily.InterNetwork)
                return b[0]==10 || (b[0]==172&&b[1]>=16&&b[1]<=31) || (b[0]==192&&b[1]==168) || (b[0]==169&&b[1]==254);
            return ip.IsIPv6LinkLocal || (b[0]&0xfe)==0xfc;
        }
        public void Save()
        {
            SecurityAccess.CheckPermission(Ids.ManagementPlugin,"ConfigureSearch");
            Validate(); var item=Item();item.Properties["SearchSettings"]=JsonConvert.SerializeObject(this);
            VideoOS.Platform.Configuration.Instance.SaveItemConfiguration(Ids.ManagementPlugin,item);
        }
    }
    public sealed class BackendClient:IDisposable
    {
        private readonly HttpClient http;
        public BackendClient(string url,string token)
        {
            new PluginSettings {BackendUrl=url}.Validate();
            http=new HttpClient {BaseAddress=new Uri(url.TrimEnd('/')+"/"),Timeout=TimeSpan.FromSeconds(30)};
            http.DefaultRequestHeaders.Authorization=new AuthenticationHeaderValue("Bearer",token);
        }
        public async Task<JToken> Request(string path,object body=null,string method=null)
        {
            using(var request=new HttpRequestMessage(new HttpMethod(method??(body==null?"GET":"POST")),"api/"+path))
            {
                if(body!=null) request.Content=new StringContent(JsonConvert.SerializeObject(body),Encoding.UTF8,"application/json");
                using(var response=await http.SendAsync(request).ConfigureAwait(false))
                {
                    string text=await response.Content.ReadAsStringAsync().ConfigureAwait(false);
                    if(!response.IsSuccessStatusCode) throw new InvalidOperationException("Backend "+(int)response.StatusCode+": "+text);
                    return JToken.Parse(text);
                }
            }
        }
        public async Task<DeliveryResult> Deliver(string path,JToken body)
        {
            using(var request=new HttpRequestMessage(HttpMethod.Post,"api/"+path))
            {
                request.Content=new StringContent(body.ToString(Formatting.None),Encoding.UTF8,"application/json");
                try
                {
                    using(var response=await http.SendAsync(request).ConfigureAwait(false))
                    {
                        if(response.IsSuccessStatusCode)return DeliveryResult.Delivered;
                        int status=(int)response.StatusCode;
                        return status==408 || status==429 || status>=500?DeliveryResult.Retry:DeliveryResult.Reject;
                    }
                }
                catch(HttpRequestException){return DeliveryResult.Retry;}
                catch(TaskCanceledException){return DeliveryResult.Retry;}
            }
        }
        public void Dispose(){http.Dispose();}
    }
}
