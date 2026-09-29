using System;
using System.Collections.Generic;
using System.Drawing;
using VideoOS.Platform;
using VideoOS.Platform.Admin;
using VideoOS.Platform.Background;

namespace MilestoneSearch
{
    public sealed class EventServerPluginDefinition:PluginDefinition
    {
        public override Guid Id=>Ids.EventServerPlugin;
        public override string Name=>"Milestone Search Event Collector";
        public override string Manufacturer=>"Search Engine Project";
        public override string VersionString=>"0.3.0";
        public override Image Icon=>SystemIcons.Information.ToBitmap();
        public override List<ItemNode> ItemNodes=>new List<ItemNode>();
        public override List<BackgroundPlugin> BackgroundPlugins=>new List<BackgroundPlugin>{new CollectorPlugin()};
        public override void Init(){}
        public override void Close(){}
    }
}
