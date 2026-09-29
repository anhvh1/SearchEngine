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
        // Smart Client's own theme is dark; default label colors would render invisible text on this background,
        // which is why past failures here looked like a blank tab instead of a readable error.
        private readonly Label status=new Label {Dock=DockStyle.Top,AutoSize=false,Height=28,TextAlign=System.Drawing.ContentAlignment.MiddleLeft,
            Padding=new Padding(10,0,10,0),ForeColor=Color.White,BackColor=Color.FromArgb(0x7a,0x1f,0x1f),Font=new Font("Segoe UI",9F,FontStyle.Bold),Visible=false};
        private readonly string tab;
        private string allowedOrigin;
        private VideoOS.Platform.Login.LoginSettings login;
        public ConsolePanel(string tab="search") {this.tab=tab;BackColor=Color.FromArgb(0x08,0x0c,0x14);Controls.Add(browser);Controls.Add(status);}
        private void Fail(string message,Exception exception=null)
        {
            status.BackColor=Color.FromArgb(0x7a,0x1f,0x1f); status.Text=message; status.Visible=true;
            if(exception!=null)PluginLog.Error(exception); else PluginLog.Info("Search console: "+message);
        }
        private void Info(string message)
        {
            status.BackColor=Color.FromArgb(0x14,0x1d,0x2e); status.Text=message; status.Visible=true;
        }
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
                    browser.CoreWebView2.NavigationCompleted+=(sender,args)=>{
                        if(args.IsSuccess){status.Visible=false;SendIdentity();return;}
                        // WebView2's own network-error interstitial would otherwise render as a near-invisible sliver
                        // on top of Smart Client's dark background; replace it with a readable page of our own.
                        string message=$"Không kết nối được máy chủ backend tại {allowedOrigin}. Kiểm tra địa chỉ (kèm cổng, mặc định :8765) trong Management Client, và máy chủ backend đang chạy. Mã lỗi: {args.WebErrorStatus}.";
                        Fail(message);
                        browser.CoreWebView2.NavigateToString(
                            "<body style=\"margin:0;height:100vh;display:flex;align-items:center;justify-content:center;background:#080c14;"+
                            "color:#eef3fb;font:15px 'Segoe UI',Arial,sans-serif;text-align:center;padding:32px\"><div style=\"max-width:420px\">"+
                            "<div style=\"font-size:12px;letter-spacing:2px;font-weight:700;color:#ff5f6d;margin-bottom:10px\">KHÔNG KẾT NỐI ĐƯỢC</div>"+
                            "<div>"+System.Net.WebUtility.HtmlEncode(message)+"</div></div></body>");
                    };
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
                        catch(Exception ex){Fail("Playback: "+ex.Message,ex);}
                    };
                    browser.CoreWebView2.PermissionRequested+=(sender,args)=>{
                        // Microphone remains an explicit user decision; all other device permissions are denied.
                        if(args.PermissionKind!=CoreWebView2PermissionKind.Microphone)args.State=CoreWebView2PermissionState.Deny;
                    };
                }
                Info($"Đang kết nối tới {allowedOrigin}…");
                browser.Source=new Uri(url.TrimEnd('/')+"/?tab="+tab);
            }
            catch(Exception ex){Fail("Search console unavailable: "+ex.Message,ex);}
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
}
