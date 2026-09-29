using System;
using System.IO;
using System.Linq;
using System.Text;
using System.Threading;
using System.Threading.Tasks;

namespace MilestoneSearch
{
    public sealed class Outbox
    {
        private readonly string directory;
        private long quota;
        private readonly object gate = new object();
        private readonly SemaphoreSlim drain = new SemaphoreSlim(1, 1);
        public Outbox(string directory, long quota) {this.directory=directory;this.quota=quota;Directory.CreateDirectory(directory);}
        public void SetQuota(long value) {lock(gate){quota=value;}}
        public void Enqueue(string body)
        {
            byte[] bytes=Encoding.UTF8.GetBytes(body);
            lock(gate)
            {
                long used=new DirectoryInfo(directory).EnumerateFiles().Sum(f=>f.Length);
                if(used+bytes.Length>quota) throw new IOException("Collector outbox quota exceeded; delivery coverage is incomplete.");
                string path=Path.Combine(directory, DateTime.UtcNow.Ticks.ToString("D19")+"-"+Guid.NewGuid().ToString("N"));
                using(var file=new FileStream(path+".tmp",FileMode.CreateNew,FileAccess.Write,FileShare.None))
                {file.Write(bytes,0,bytes.Length);file.Flush(true);}
                File.Move(path+".tmp",path+".json");
            }
        }
        public async Task DrainOnce(Func<string,Task<bool>> send)
        {
            await DrainOnce(async body=>await send(body)?DeliveryResult.Delivered:DeliveryResult.Retry);
        }
        public async Task DrainOnce(Func<string,Task<DeliveryResult>> send)
        {
            if(!await drain.WaitAsync(0)) return;
            try
            {
                foreach(string path in Directory.GetFiles(directory,"*.json").OrderBy(x=>x).Take(100))
                {
                    var result=await send(File.ReadAllText(path,Encoding.UTF8));
                    if(result==DeliveryResult.Retry) break;
                    if(result==DeliveryResult.Reject)
                    {
                        string rejected=Path.Combine(directory,"dead-letter");Directory.CreateDirectory(rejected);
                        File.Move(path,Path.Combine(rejected,Path.GetFileName(path)));
                    }
                    else File.Delete(path);
                }
            }
            finally {drain.Release();}
        }
    }
}
