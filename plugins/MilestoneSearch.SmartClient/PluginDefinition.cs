using System;
using System.Collections.Generic;
using System.Drawing;
using VideoOS.Platform;
using VideoOS.Platform.Admin;
using VideoOS.Platform.Client;

namespace MilestoneSearch
{
    public sealed class SmartClientPluginDefinition:PluginDefinition
    {
        public override Guid Id=>Ids.SmartClientPlugin;
        public override string Name=>"Milestone Search Smart Client";
        public override string Manufacturer=>"Search Engine Project";
        public override string VersionString=>"0.3.0";
        public override Image Icon=>SystemIcons.Information.ToBitmap();
        public override List<ItemNode> ItemNodes=>new List<ItemNode>();
        public override List<WorkSpacePlugin> WorkSpacePlugins=>new List<WorkSpacePlugin>{new SearchWorkspace()};
        public override void Init(){}
        public override void Close(){}
    }
}
