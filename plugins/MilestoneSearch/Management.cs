using System;
using System.Collections.Generic;
using System.Drawing;
using System.Windows.Forms;
using VideoOS.Platform;
using VideoOS.Platform.Admin;
using VideoOS.Platform.Util;

namespace MilestoneSearch
{
    public sealed class SearchItemManager:ItemManager
    {
        private ManagementPanel panel;
        public override UserControl GenerateDetailUserControl(){ReleaseUserControl();return panel=new ManagementPanel();}
        public override void ReleaseUserControl(){panel?.Dispose();panel=null;}
        public override void FillUserControl(Item item){CurrentItem=item;panel?.LoadSettings();}
        public override void ClearUserControl(){CurrentItem=null;}
        public override bool ValidateAndSaveUserControl(){return panel==null || panel.SaveSettings();}
        public override string GetItemName()=>"Milestone Search";
        public override void SetItemName(string name){}
        public override List<Item> GetItems()=>new List<Item>{PluginSettings.Item()};
        public override List<Item> GetItems(Item parent)=>GetItems();
        public override Item GetItem(FQID fqid)=>fqid==null || fqid.ObjectId==Ids.Configuration?PluginSettings.Item():null;
        public override Item CreateItem(Item parent,FQID suggested)=>PluginSettings.Item();
        public override void DeleteItem(Item item){throw new InvalidOperationException("Search configuration cannot be deleted; disable the collector instead.");}
        public override bool IsContextMenuValid(string command)=>false;
        public override void Close(){ReleaseUserControl();}
    }
    public sealed class ManagementPanel:UserControl
    {
        private readonly TextBox url=new TextBox {Width=290};
        private readonly TextBox site=new TextBox {Width=100};
        private readonly CheckBox enabled=new CheckBox {Text="Enable collector",AutoSize=true};
        private readonly Button save=new Button {Text="Save connection",AutoSize=true};
        private readonly Label status=new Label {AutoSize=true};
        private readonly ConsolePanel console=new ConsolePanel("profiles");
        public ManagementPanel()
        {
            Dock=DockStyle.Fill;
            var bar=new FlowLayoutPanel {Dock=DockStyle.Top,Height=90,Padding=new Padding(8),AutoScroll=true};
            bar.Controls.AddRange(new Control[]{new Label {Text="Backend URL",AutoSize=true},url,new Label {Text="Site ID",AutoSize=true},site,enabled,save,status});
            console.Dock=DockStyle.Fill;Controls.Add(console);Controls.Add(bar);
            save.Click+=(sender,args)=>SaveSettings();
        }
        public void LoadSettings()
        {
            try
            {
                var settings=PluginSettings.Load();url.Text=settings.BackendUrl;site.Text=settings.SiteId;enabled.Checked=settings.Enabled;
                bool canConfigure=true;try{SecurityAccess.CheckPermission(Ids.ManagementPlugin,"ConfigureSearch");}catch{canConfigure=false;}
                url.Enabled=site.Enabled=enabled.Enabled=save.Enabled=canConfigure;
                status.Text=canConfigure?"Backend administration requires a separate administrator login.":"Milestone ConfigureSearch permission required.";
                console.Navigate(settings.BackendUrl);
            }
            catch(Exception ex){status.Text=ex.Message;}
        }
        public bool SaveSettings()
        {
            try
            {
                var settings=new PluginSettings {BackendUrl=url.Text.Trim(),SiteId=site.Text.Trim(),Enabled=enabled.Checked};
                settings.Save();status.Text="Saved. Event Server reloads on configuration change.";console.Navigate(settings.BackendUrl);return true;
            }
            catch(Exception ex){status.Text=ex.Message;return false;}
        }
    }
}
