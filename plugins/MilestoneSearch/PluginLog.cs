using System;
using System.IO;
using System.Text;
using VideoOS.Platform;

namespace MilestoneSearch
{
    internal static class PluginLog
    {
        private static readonly object Gate=new object();
        private static readonly string DirectoryPath=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData),"MilestoneSearch","logs");
        private static readonly string FilePath=Path.Combine(DirectoryPath,"collector.log");

        public static void Info(string message)=>Write(false,message);
        public static void Error(Exception exception)=>Write(true,exception.GetType().Name+": "+exception.Message);

        private static void Write(bool error,string message)
        {
            try{EnvironmentManager.Instance.Log(error,"MilestoneSearch",message,null);}catch{}
            try
            {
                lock(Gate)
                {
                    Directory.CreateDirectory(DirectoryPath);
                    if(File.Exists(FilePath) && new FileInfo(FilePath).Length>5*1024*1024)
                    {
                        string previous=FilePath+".1";
                        if(File.Exists(previous))File.Delete(previous);
                        File.Move(FilePath,previous);
                    }
                    File.AppendAllText(FilePath,DateTimeOffset.Now.ToString("o")+" "+(error?"ERROR":"INFO")+" "+message+Environment.NewLine,Encoding.UTF8);
                }
            }
            catch{}
        }
    }
}
