using System;
using System.Collections.Generic;
using System.Drawing;
using System.IO;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;
using VideoOS.Platform.Client;
using VideoOS.Platform;
using VideoOS.Platform.Messaging;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace MilestoneSearch
{
    public sealed class ConsolePanel:UserControl
    {
        private readonly WebView2 browser=new WebView2 {Dock=DockStyle.Fill};
        private readonly Label status=new Label {Dock=DockStyle.Top,AutoSize=true};
        private readonly string tab;
        private string allowedOrigin;
        private VideoOS.Platform.Login.LoginSettings login;
        public ConsolePanel(string tab="search") {this.tab=tab;Controls.Add(browser);Controls.Add(status);}
        public async void Navigate(string url)
        {
            try
            {
                new PluginSettings {BackendUrl=url}.Validate();
                allowedOrigin=new Uri(url).GetLeftPart(UriPartial.Authority);
                string cache=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),"MilestoneSearch","WebView2");
                var environment=await CoreWebView2Environment.CreateAsync(null,cache);
                if(IsDisposed)return;
                if(browser.CoreWebView2==null)
                {
                    await browser.EnsureCoreWebView2Async(environment);
                    browser.CoreWebView2.Settings.AreDevToolsEnabled=false;
                    browser.CoreWebView2.Settings.AreDefaultContextMenusEnabled=false;
                    browser.CoreWebView2.NavigationStarting+=(sender,args)=>{
                        if(!Uri.TryCreate(args.Uri,UriKind.Absolute,out var target) || target.GetLeftPart(UriPartial.Authority)!=allowedOrigin) args.Cancel=true;
                    };
                    browser.CoreWebView2.NewWindowRequested+=(sender,args)=>args.Handled=true;
                    browser.CoreWebView2.NavigationCompleted+=(sender,args)=>{if(args.IsSuccess)SendIdentity();};
                    login=VideoOS.Platform.Login.LoginSettingsCache.GetLoginSettings(EnvironmentManager.Instance.MasterSite);
                    if(login!=null)login.TokenChangedEvent+=OnTokenChanged;
                    browser.CoreWebView2.WebMessageReceived+=(sender,args)=>{
                        try
                        {
                            if(tab!="search" || new Uri(args.Source).GetLeftPart(UriPartial.Authority)!=allowedOrigin)return;
                            var request=JObject.Parse(args.WebMessageAsJson);
                            if((string)request["action"]!="playback")return;
                            var cameraId=Guid.Parse((string)request["camera_id"]);
                            var time=DateTimeOffset.Parse((string)request["time"],System.Globalization.CultureInfo.InvariantCulture);
                            var camera=Configuration.Instance.GetItem(cameraId,VideoOS.Platform.Kind.Camera);
                            if(camera==null)throw new InvalidOperationException("Camera is unavailable to the current Milestone user.");
                            EnvironmentManager.Instance.SendMessage(new VideoOS.Platform.Messaging.Message(
                                MessageId.SmartClient.ShowCamerasInFloatingWindowCommand,
                                new ShowCamerasInFloatingWindowData {Cameras=new[]{camera},Mode=Mode.ClientPlayback,BrowseTime=time.UtcDateTime}));
                        }
                        catch(Exception ex){status.Text="Playback: "+ex.Message;}
                    };
                    browser.CoreWebView2.PermissionRequested+=(sender,args)=>{
                        // Microphone remains an explicit user decision; all other device permissions are denied.
                        if(args.PermissionKind!=CoreWebView2PermissionKind.Microphone)args.State=CoreWebView2PermissionState.Deny;
                    };
                }
                browser.Source=new Uri(url.TrimEnd('/')+"/?tab="+tab);
                status.Text="";
            }
            catch(Exception ex){status.Text="Search console unavailable: "+ex.Message;}
        }
        private void OnTokenChanged(object sender,EventArgs args)
        {
            if(IsDisposed)return;
            try{BeginInvoke((Action)SendIdentity);}catch(InvalidOperationException){}
        }
        // Signs the console in as the current Milestone user; the backend verifies the token with Milestone.
        private void SendIdentity()
        {
            try
            {
                string token=login?.IdentityTokenCache?.Token;
                if(browser.CoreWebView2==null || string.IsNullOrEmpty(token))return;
                if(new Uri(browser.CoreWebView2.Source).GetLeftPart(UriPartial.Authority)!=allowedOrigin)return;
                browser.CoreWebView2.PostWebMessageAsJson(new JObject {["type"]="milestone-token",["token"]=token}.ToString(Formatting.None));
            }
            catch(Exception ex){status.Text="Sign-in: "+ex.Message;}
        }
        protected override void Dispose(bool disposing)
        {
            if(disposing && login!=null)login.TokenChangedEvent-=OnTokenChanged;
            base.Dispose(disposing);
        }
    }
    public sealed class SearchWorkspace:WorkSpacePlugin
    {
        public override Guid Id=>Ids.Workspace;
        public override string Name=>"AI Search";
        public override bool IsSetupStateSupported=>false;
        public override void Init()
        {
            ViewAndLayoutItem.Layout=new[]{new Rectangle(0,0,1000,1000)};
            ViewAndLayoutItem.Name=Name;
            ViewAndLayoutItem.InsertViewItemPlugin(0,new SearchViewPlugin(),new Dictionary<string,string>());
        }
        public override void Close(){}
    }
    public sealed class SearchViewPlugin:ViewItemPlugin
    {
        public override Guid Id=>Ids.View;
        public override string Name=>"AI Search";
        public override ViewItemManager GenerateViewItemManager()=>new SearchViewManager();
        public override void Init(){}
        public override void Close(){}
    }
    public sealed class SearchViewManager:ViewItemManager
    {
        public SearchViewManager():base("AI Search"){}
        public override ViewItemWpfUserControl GenerateViewItemWpfUserControl()=>new SearchViewControl();
    }
    public sealed class SearchViewControl:ViewItemWpfUserControl
    {
        private ConsolePanel panel;
        public override void Init()
        {
            panel=new ConsolePanel {Dock=DockStyle.Fill};
            Content=new System.Windows.Forms.Integration.WindowsFormsHost {Child=panel};
            try{panel.Navigate(PluginSettings.Load().BackendUrl);}catch(Exception ex){Content=new System.Windows.Controls.TextBlock {Text=ex.Message};}
        }
        public override void Close(){panel?.Dispose();panel=null;}
    }
}
