using System;
using System.IO;
using System.Linq;
using System.Threading.Tasks;
using VideoOS.Platform;
using VideoOS.Platform.Data;

namespace MilestoneSearch.Tests
{
    class Program
    {
        static int Main()
        {
            var directory = Path.Combine(Path.GetTempPath(), "milestone-search-test-" + Guid.NewGuid());
            try
            {
                Directory.CreateDirectory(directory);
                Run(directory).GetAwaiter().GetResult();
                Console.WriteLine("PASS: durable outbox retry/restart, quota, event mapping and UTC validation");
                return 0;
            }
            catch(Exception ex) { Console.Error.WriteLine(ex); return 1; }
            finally { Directory.Delete(directory, true); }
        }
        static void Check(bool condition, string message) { if(!condition) throw new Exception(message); }
        static string Pem(string subject, bool ca)
        {
            using(var key = System.Security.Cryptography.ECDsa.Create())
            {
                var request = new System.Security.Cryptography.X509Certificates.CertificateRequest(subject, key, System.Security.Cryptography.HashAlgorithmName.SHA256);
                request.CertificateExtensions.Add(new System.Security.Cryptography.X509Certificates.X509BasicConstraintsExtension(ca, false, 0, true));
                var cert = request.CreateSelfSigned(DateTimeOffset.UtcNow.AddMinutes(-1), DateTimeOffset.UtcNow.AddDays(1));
                return "-----BEGIN CERTIFICATE-----\n" + Convert.ToBase64String(cert.RawData) + "\n-----END CERTIFICATE-----\n";
            }
        }
        static async Task Run(string directory)
        {
            foreach(var ok in new[]{"http://127.0.0.1:8765","http://192.168.100.145:8765","http://10.0.0.5:8765","http://172.20.1.1:8765","https://search.example.com"})
                Check(PluginSettings.IsPrivateNetwork(new Uri(ok)) || ok.StartsWith("https"), "Must accept "+ok);
            foreach(var bad in new[]{"http://8.8.8.8:8765","http://172.32.0.1:8765","http://search.local:8765"})
                Check(!PluginSettings.IsPrivateNetwork(new Uri(bad)), "Must reject plain HTTP "+bad);
            var outbox = new Outbox(directory, 1024 * 1024);
            outbox.Enqueue("{\"one\":1}");
            await outbox.DrainOnce(body => Task.FromResult(false));
            Check(Directory.GetFiles(directory, "*.json").Length == 1, "Failed delivery must remain durable");
            var restarted = new Outbox(directory, 1024 * 1024);
            string delivered = null;
            await restarted.DrainOnce(body => {delivered=body; return Task.FromResult(true);});
            Check(delivered == "{\"one\":1}", "Restart must deliver the original payload");
            Check(Directory.GetFiles(directory, "*.json").Length == 0, "Acknowledged delivery must be removed");
            restarted.Enqueue("bad"); restarted.Enqueue("good");
            await restarted.DrainOnce(body => Task.FromResult(body == "bad" ? DeliveryResult.Reject : DeliveryResult.Delivered));
            Check(Directory.GetFiles(Path.Combine(directory,"dead-letter"),"*.json").Length == 1,"Permanent rejection must be quarantined");
            Check(Directory.GetFiles(directory,"*.json").Length == 0,"Rejected message must not block following messages");
            bool quota = false;
            try { new Outbox(directory, 2).Enqueue("123"); } catch(IOException) {quota=true;}
            Check(quota, "Quota must fail visibly");
            var id = Guid.NewGuid(); var source = Guid.NewGuid();
            var alarm = new Alarm {EventHeader = new EventHeader {ID=id, Timestamp=new DateTime(2026,9,16,8,0,0,DateTimeKind.Utc),
                Type="Analytics", Message="FACEME.UNKNOWN_PERSON", Source=new EventSource {FQID=new FQID {ObjectId=source}, Name="Gate"}},
                Description="Unknown at gate", StateName="New"};
            var mapped=EnvelopeMapper.Map(alarm,"lab");
            Check((string)mapped["source_guid"]==id.ToString(), "Must preserve Milestone identity");
            Check((string)mapped["source_id"]==source.ToString(), "Must preserve source identity");
            Check((string)mapped["kind"]=="alarm", "Must distinguish alarm from event");
            alarm.EventHeader.Timestamp=DateTime.SpecifyKind(alarm.EventHeader.Timestamp,DateTimeKind.Unspecified);
            // Installed SDK normalizes Timestamp in its setter; mapper must preserve that UTC instant.
            var normalized=EnvelopeMapper.Map(alarm,"lab");
            Check((string)normalized["occurred_at"]==alarm.EventHeader.Timestamp.ToUniversalTime().ToString("o"),"Must preserve SDK timestamp semantics");

            // i-PRO Active Guard can attach the detected face/plate photo straight to the Alarm's SnapshotList
            // (Management Client: "Notification to VMS Server" -> "Image on Alarm Manager"); the mapper must lift
            // it into payload.Snapshot.Image so the backend's pop_images() picks it up, or search never shows a
            // photo for alarms even when Milestone's own Alarm Manager clearly has one (regression: reported live).
            var withSnapshot = new Alarm {EventHeader = new EventHeader {ID=Guid.NewGuid(), Timestamp=DateTime.UtcNow,
                Type="Analytics", Message="FACEME.REGISTERED", Source=new EventSource {FQID=new FQID {ObjectId=source}, Name="Gate"}}};
            var bytes = new byte[]{1,2,3,4,5};
            withSnapshot.SnapshotList = new SnapshotList();
            withSnapshot.SnapshotList.Add(new Snapshot{Image=null});
            withSnapshot.SnapshotList.Add(new Snapshot{Image=bytes});
            var mappedWithSnapshot = EnvelopeMapper.Map(withSnapshot,"lab");
            Check((string)mappedWithSnapshot["payload"]["Snapshot"]["Image"] == Convert.ToBase64String(bytes),
                "Must lift the alarm's snapshot image into payload.Snapshot.Image, skipping empty entries");

            // Only the backend's own CA may ever be installed as a trusted root on operator PCs.
            Check(TrustedCa.Parse(Pem("CN=Search Engine Local CA (srv)", true)).Subject.Contains("Search Engine Local CA"), "Must accept the backend CA");
            foreach(var bad in new[]{Pem("CN=Search Engine Local CA (srv)", false), Pem("CN=Some Other Root", true)})
            {
                bool rejected = false;
                try { TrustedCa.Parse(bad); } catch(FormatException) { rejected = true; }
                Check(rejected, "Must reject a certificate that is not the backend CA");
            }
        }
    }
}
