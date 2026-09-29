using System;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using VideoOS.Platform.Data;

namespace MilestoneSearch
{
    public static class EnvelopeMapper
    {
        // updatedUtc marks a newer revision of an alarm fetched after a change notification.
        public static JObject Map(object input,string site,DateTime? updatedUtc=null)
        {
            var alarm=input as Alarm;
            var ev=input as BaseEvent;
            var header=alarm?.EventHeader ?? ev?.EventHeader;
            if(header==null || header.ID==Guid.Empty || header.Source?.FQID==null)
                throw new ArgumentException("Notification lacks a stable event/source identity.");
            if(header.Timestamp.Kind==DateTimeKind.Unspecified)
                throw new ArgumentException("SDK timestamp timezone is unspecified; configure source timezone explicitly before ingestion.");
            string stamp=header.Timestamp.ToUniversalTime().ToString("o");
            var serializer=JsonSerializer.Create(new JsonSerializerSettings {ReferenceLoopHandling=ReferenceLoopHandling.Ignore,MaxDepth=32});
            var payload=new JObject { ["header"]=JObject.FromObject(header,serializer) };
            if(alarm!=null)
            {
                if(alarm.ObjectList!=null) payload["objects"]=JToken.FromObject(alarm.ObjectList,serializer);
                if(alarm.Vendor!=null) payload["vendor"]=JToken.FromObject(alarm.Vendor,serializer);
                if(alarm.RuleList!=null) payload["rules"]=JToken.FromObject(alarm.RuleList,serializer);
                if(alarm.ReferenceList!=null) payload["references"]=JToken.FromObject(alarm.ReferenceList,serializer);
                payload["category"]=alarm.CategoryName;
                payload["assignedTo"]=alarm.AssignedTo;
            }
            else if(ev!=null)
            {
                // Preserve derived event fields without copying alarm snapshot binary data.
                payload["event"]=JToken.FromObject(ev,serializer);
            }
            return new JObject {
                ["site_id"]=site,["kind"]=alarm!=null?"alarm":"event",["source_guid"]=header.ID.ToString(),
                ["source_id"]=header.Source.FQID.ObjectId.ToString(),
                ["event_type"]=header.MessageId!=Guid.Empty?header.MessageId.ToString():header.Type??"unknown",
                ["occurred_at"]=stamp,["updated_at"]=updatedUtc.HasValue?updatedUtc.Value.ToUniversalTime().ToString("o"):stamp,["message"]=header.Message??"",
                ["description"]=alarm?.Description??header.Source.Description??"",["state"]=alarm?.StateName??"",
                ["priority"]=header.PriorityName??header.Priority.ToString(),["location"]=alarm?.Location??"",
                ["camera_id"]=header.Source.FQID.Kind==VideoOS.Platform.Kind.Camera?header.Source.FQID.ObjectId.ToString():null,
                ["payload"]=payload
            };
        }
    }
}
