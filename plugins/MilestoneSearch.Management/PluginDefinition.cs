using System;
using System.Collections.Generic;
using System.Drawing;
using VideoOS.Platform;
using VideoOS.Platform.Admin;

namespace MilestoneSearch
{
    public sealed class ManagementPluginDefinition:PluginDefinition
    {
        private readonly List<ItemNode> nodes=new List<ItemNode>();
        public override Guid Id=>Ids.ManagementPlugin;
        public override string Name=>"Milestone Search Management";
        public override string Manufacturer=>"Search Engine Project";
        public override string VersionString=>"0.3.0";
        public override Image Icon=>SystemIcons.Information.ToBitmap();
        public override List<ItemNode> ItemNodes=>nodes;
        public override List<SecurityAction> SecurityActions {get=>new List<SecurityAction>{new SecurityAction("ConfigureSearch","Configure search integration")};set{}}
        public override void Init(){nodes.Clear();nodes.Add(new ItemNode(Ids.Kind,Guid.Empty,"Milestone Search",Icon,"Milestone Search",Icon,Category.Text,false,ItemsAllowed.One,new SearchItemManager(),null));}
        public override void Close(){nodes.Clear();}
    }
}
