using System;
using System.IO;
using System.Linq;
using System.Net;
using System.Net.Http;
using System.Security.Cryptography;
using System.Security.Cryptography.X509Certificates;
using System.Security.Principal;
using System.Text;
using System.Threading.Tasks;
using Newtonsoft.Json.Linq;

namespace MilestoneSearch
{
    // The backend's own CA lets browsers open the console over HTTPS (needed for the microphone). It reaches the
    // operator PCs through Milestone's authenticated configuration, never straight from the network: the Event
    // Server plugin fetches it from the backend signed with the collector token and publishes it; the Smart Client
    // plugin installs it into the Windows trusted roots if it is not there yet.
    public static class TrustedCa
    {
        private const string Property="BackendCa";
        private const string ExpectedName="Search Engine Local CA";

        public static X509Certificate2 Parse(string pem)
        {
            const string begin="-----BEGIN CERTIFICATE-----",end="-----END CERTIFICATE-----";
            int start=pem.IndexOf(begin,StringComparison.Ordinal),stop=pem.IndexOf(end,StringComparison.Ordinal);
            if(start<0 || stop<start) throw new FormatException("Not a PEM certificate.");
            var cert=new X509Certificate2(Convert.FromBase64String(pem.Substring(start+begin.Length,stop-start-begin.Length)));
            bool isCa=cert.Extensions.OfType<X509BasicConstraintsExtension>().Any(e=>e.CertificateAuthority);
            if(!isCa || cert.Subject.IndexOf(ExpectedName,StringComparison.Ordinal)<0)
                throw new FormatException("Not the Search Engine backend CA: "+cert.Subject);
            return cert;
        }

        // ---------- Event Server: publish ----------
        // The backend signs the CA with this collector's token (HMAC), so a machine between the Event Server and a
        // backend elsewhere on the LAN cannot slip in its own CA even though the connection is plain HTTP.
        public static async Task Publish(PluginSettings settings,string collectorToken)
        {
            if(string.IsNullOrWhiteSpace(collectorToken)) return;
            JObject body;
            using(var http=new HttpClient {BaseAddress=new Uri(settings.BackendUrl.TrimEnd('/')+"/"),Timeout=TimeSpan.FromSeconds(15)})
            {
                http.DefaultRequestHeaders.Authorization=new System.Net.Http.Headers.AuthenticationHeaderValue("Bearer",collectorToken);
                using(var response=await http.GetAsync("api/collector/ca").ConfigureAwait(false))
                {
                    if(response.StatusCode==HttpStatusCode.NotFound) return;   // HTTPS off, or an administrator-supplied certificate
                    response.EnsureSuccessStatusCode();
                    body=JObject.Parse(await response.Content.ReadAsStringAsync().ConfigureAwait(false));
                }
            }
            string pem=(string)body["pem"];
            if(!Verify(pem,(string)body["mac"],collectorToken)) throw new CryptographicException("Backend CA signature does not match the collector token; not published.");
            var cert=Parse(pem);
            var item=PluginSettings.Item();
            if(item.Properties.TryGetValue(Property,out var current) && current==pem) return;
            item.Properties[Property]=pem;
            VideoOS.Platform.Configuration.Instance.SaveItemConfiguration(Ids.ManagementPlugin,item);
            PluginLog.Info("Backend CA published to Milestone configuration: "+cert.Thumbprint);
        }

        public static bool Verify(string pem,string mac,string token)
        {
            if(pem==null || mac==null) return false;
            byte[] expected;
            using(var h=new HMACSHA256(Encoding.UTF8.GetBytes(token))) expected=h.ComputeHash(Encoding.UTF8.GetBytes(pem));
            string hex=BitConverter.ToString(expected).Replace("-","").ToLowerInvariant();
            if(hex.Length!=mac.Length) return false;
            int diff=0;
            for(int i=0;i<hex.Length;i++) diff|=hex[i]^char.ToLowerInvariant(mac[i]);
            return diff==0;
        }

        // ---------- Smart Client: install once ----------
        public static void EnsureTrusted()
        {
            var item=PluginSettings.Item();
            if(!item.Properties.TryGetValue(Property,out var pem) || string.IsNullOrWhiteSpace(pem)) return;
            var cert=Parse(pem);
            if(IsTrusted(cert)) return;
            string declined=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),"MilestoneSearch","ca-declined.txt");
            if(File.Exists(declined) && File.ReadAllText(declined).Trim()==cert.Thumbprint) return;
            // Machine store when elevated (silent); otherwise the user's store, where Windows itself asks once to confirm.
            var location=new WindowsPrincipal(WindowsIdentity.GetCurrent()).IsInRole(WindowsBuiltInRole.Administrator)?StoreLocation.LocalMachine:StoreLocation.CurrentUser;
            try
            {
                using(var store=new X509Store(StoreName.Root,location)){store.Open(OpenFlags.ReadWrite);store.Add(cert);}
                PluginLog.Info("Backend CA installed in "+location+" trusted roots: "+cert.Thumbprint);
            }
            catch(CryptographicException ex)
            {
                // The user answered No to Windows' confirmation: do not ask again for this CA (delete the file to re-ask).
                Directory.CreateDirectory(Path.GetDirectoryName(declined));
                File.WriteAllText(declined,cert.Thumbprint);
                PluginLog.Info("Backend CA not installed ("+ex.Message+"); will not ask again for "+cert.Thumbprint);
            }
        }

        private static bool IsTrusted(X509Certificate2 cert)
        {
            foreach(var location in new[]{StoreLocation.CurrentUser,StoreLocation.LocalMachine})
                using(var store=new X509Store(StoreName.Root,location))
                {
                    store.Open(OpenFlags.ReadOnly);
                    if(store.Certificates.Find(X509FindType.FindByThumbprint,cert.Thumbprint,false).Count>0) return true;
                }
            return false;
        }
    }
}
