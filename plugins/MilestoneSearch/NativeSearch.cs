using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Text;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Windows.Threading;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NAudio.Wave;
using VideoOS.Platform;
using VideoOS.Platform.Client;
using VideoOS.Platform.Messaging;

namespace MilestoneSearch
{
    // ---------- backend HTTP session: same REST contract as web/app.js, called directly (no WebView2) ----------
    internal sealed class BackendSession : IDisposable
    {
        private readonly HttpClient http;
        public string Token { get; private set; }
        public BackendSession(string url)
        {
            new PluginSettings { BackendUrl = url }.Validate();
            http = new HttpClient { BaseAddress = new Uri(url.TrimEnd('/') + "/"), Timeout = TimeSpan.FromSeconds(30) };
        }
        public async Task<JObject> SignIn(string milestoneToken)
        {
            var response = (JObject)await Send(HttpMethod.Post, "api/session", new JObject { ["token"] = milestoneToken }, authorized: false);
            Token = (string)response["token"];
            http.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", Token);
            return response;
        }
        public async Task<JObject> Capabilities() => (JObject)await Send(HttpMethod.Get, "api/capabilities");
        public async Task<JObject> Ask(string text, int offset, int limit, int tzOffsetMinutes) =>
            (JObject)await Send(HttpMethod.Post, "api/ask", new JObject { ["text"] = text, ["tz_offset_minutes"] = tzOffsetMinutes, ["limit"] = limit, ["offset"] = offset });
        public async Task<byte[]> Image(string key)
        {
            using (var response = await http.GetAsync("api/records/" + Uri.EscapeDataString(key) + "/image"))
                return response.IsSuccessStatusCode ? await response.Content.ReadAsByteArrayAsync() : null;
        }
        public async Task<string> Transcribe(byte[] wav)
        {
            using (var content = new MultipartFormDataContent())
            {
                var audio = new ByteArrayContent(wav);
                audio.Headers.ContentType = new MediaTypeHeaderValue("audio/wav");
                content.Add(audio, "file", "voice.wav");
                using (var response = await http.PostAsync("api/transcribe", content))
                {
                    string text = await response.Content.ReadAsStringAsync();
                    if (!response.IsSuccessStatusCode) throw new InvalidOperationException(Detail(text));
                    return (string)JObject.Parse(text)["transcript"];
                }
            }
        }
        public async Task<JObject> AskImage(byte[] bytes, string fileName, int tzOffsetMinutes)
        {
            using (var content = new MultipartFormDataContent())
            {
                var file = new ByteArrayContent(bytes);
                file.Headers.ContentType = new MediaTypeHeaderValue("image/jpeg");
                content.Add(file, "file", fileName);
                using (var response = await http.PostAsync("api/ask/image?tz_offset_minutes=" + tzOffsetMinutes, content))
                {
                    string text = await response.Content.ReadAsStringAsync();
                    if (!response.IsSuccessStatusCode) throw new InvalidOperationException(Detail(text));
                    return JObject.Parse(text);
                }
            }
        }
        private async Task<JToken> Send(HttpMethod method, string path, JToken body = null, bool authorized = true)
        {
            using (var request = new HttpRequestMessage(method, path))
            {
                if (body != null) request.Content = new StringContent(body.ToString(Formatting.None), Encoding.UTF8, "application/json");
                using (var response = await http.SendAsync(request))
                {
                    string text = await response.Content.ReadAsStringAsync();
                    if (!response.IsSuccessStatusCode) throw new InvalidOperationException(Detail(text));
                    return string.IsNullOrEmpty(text) ? null : JToken.Parse(text);
                }
            }
        }
        private static string Detail(string text)
        {
            try { var d = JObject.Parse(text)["detail"]; return d != null && d.Type != JTokenType.String ? d.ToString() : (string)d ?? text; }
            catch { return text; }
        }
        public void Dispose() => http.Dispose();
    }

    // ---------- microphone capture: offline WAV recording, sent to the backend's own transcription model ----------
    internal sealed class MicRecorder : IDisposable
    {
        private WaveInEvent waveIn;
        private WaveFileWriter writer;
        private string path;
        public bool Recording => waveIn != null;
        public void Start()
        {
            path = Path.Combine(Path.GetTempPath(), "mssearch-" + Guid.NewGuid().ToString("N") + ".wav");
            waveIn = new WaveInEvent { WaveFormat = new WaveFormat(16000, 1) };
            writer = new WaveFileWriter(path, waveIn.WaveFormat);
            waveIn.DataAvailable += (sender, args) => { try { writer?.Write(args.Buffer, 0, args.BytesRecorded); } catch (ObjectDisposedException) { } };
            waveIn.StartRecording();
        }
        public byte[] Stop()
        {
            if (waveIn == null) return null;
            waveIn.StopRecording(); waveIn.Dispose(); waveIn = null;
            writer.Dispose(); writer = null;
            byte[] bytes = File.ReadAllBytes(path);
            try { File.Delete(path); } catch { }
            return bytes;
        }
        public void Dispose() { if (Recording) { try { Stop(); } catch { } } }
    }

    // ---------- Vietnamese labels for common built-in Milestone event names (mirrors web/app.js VI map) ----------
    internal static class Labels
    {
        private static readonly Dictionary<string, string> Vi = new Dictionary<string, string>
        {
            ["motion detected"] = "Phát hiện chuyển động", ["motion stopped"] = "Hết chuyển động", ["not responding"] = "Mất kết nối",
            ["responding"] = "Kết nối lại", ["server not responding"] = "Máy chủ mất kết nối", ["server responding"] = "Máy chủ kết nối lại",
            ["intruderhuman"] = "Phát hiện người xâm nhập", ["intruder"] = "Xâm nhập", ["registered face detection"] = "Nhận diện khuôn mặt đã đăng ký",
            ["database deleting recordings before set retention size"] = "Ổ lưu trữ đầy – xóa bản ghi sớm", ["communication error"] = "Lỗi kết nối",
            ["input activated"] = "Đầu vào kích hoạt", ["output activated"] = "Đầu ra kích hoạt", ["tampering"] = "Phá hoại camera",
        };
        private static readonly Dictionary<string, string> Roles = new Dictionary<string, string>
        {
            ["person"] = "người", ["person_code"] = "mã người", ["plate"] = "biển số", ["place"] = "vị trí", ["action"] = "hành động",
            ["number"] = "số", ["duration_minutes"] = "số phút", ["vehicle"] = "phương tiện", ["color"] = "màu", ["gender"] = "giới tính",
            ["age"] = "tuổi", ["watchlist"] = "watchlist", ["code"] = "mã", ["container"] = "container", ["card"] = "thẻ", ["door"] = "cửa", ["value"] = "giá trị",
        };
        public static string Event(string name)
        {
            if (string.IsNullOrWhiteSpace(name)) return name;
            return Vi.TryGetValue(name.Trim().ToLowerInvariant(), out var vi) ? vi : name;
        }
        public static string Role(string role) => Roles.TryGetValue(role ?? "", out var vi) ? vi : "giá trị";
    }

    // ---------- palette matching the web console's dark theme ----------
    internal static class Theme
    {
        public static readonly Color Bg = Color.FromRgb(0x08, 0x0c, 0x14);
        public static readonly Color Surface = Color.FromRgb(0x11, 0x1a, 0x2b);
        public static readonly Color SurfaceStrong = Color.FromRgb(0x16, 0x21, 0x3a);
        public static readonly Color Line = Color.FromArgb(0x18, 0xff, 0xff, 0xff);
        public static readonly Color LineStrong = Color.FromArgb(0x28, 0xff, 0xff, 0xff);
        public static readonly Color Ink = Color.FromRgb(0xee, 0xf3, 0xfb);
        public static readonly Color Muted = Color.FromRgb(0x8e, 0xa0, 0xbd);
        public static readonly Color Accent = Color.FromRgb(0x4f, 0x8c, 0xff);
        public static readonly Color Accent2 = Color.FromRgb(0x8b, 0x6b, 0xff);
        public static readonly Color Red = Color.FromRgb(0xff, 0x5f, 0x6d);
        public static readonly Color Amber = Color.FromRgb(0xf5, 0xa5, 0x24);
        public static SolidColorBrush B(Color c) => new SolidColorBrush(c);
        public static Brush Grad() => new LinearGradientBrush(Accent, Accent2, new Point(0, 0), new Point(1, 1));
    }

    public sealed class SearchWorkspace : WorkSpacePlugin
    {
        public override Guid Id => Ids.Workspace;
        public override string Name => "AI Search";
        public override bool IsSetupStateSupported => false;
        public override void Init()
        {
            ViewAndLayoutItem.Layout = new[] { new System.Drawing.Rectangle(0, 0, 1000, 1000) };
            ViewAndLayoutItem.Name = Name;
            ViewAndLayoutItem.InsertViewItemPlugin(0, new SearchViewPlugin(), new Dictionary<string, string>());
        }
        public override void Close() { }
    }
    public sealed class SearchViewPlugin : ViewItemPlugin
    {
        public override Guid Id => Ids.View;
        public override string Name => "AI Search";
        public override ViewItemManager GenerateViewItemManager() => new SearchViewManager();
        public override void Init() { }
        public override void Close() { }
    }
    public sealed class SearchViewManager : ViewItemManager
    {
        public SearchViewManager() : base("AI Search") { }
        public override ViewItemWpfUserControl GenerateViewItemWpfUserControl() => new SearchViewControl();
    }

    // Native WPF search view: talks to the backend's REST API directly (BackendSession), no browser involved.
    public sealed class SearchViewControl : ViewItemWpfUserControl
    {
        private NativeSearchPanel panel;
        public override void Init()
        {
            try
            {
                panel = new NativeSearchPanel();
                Content = panel;
                panel.Start();
            }
            catch (Exception ex)
            {
                // Without this, any construction failure (e.g. a missing NAudio DLL in the install folder) is
                // swallowed by Smart Client and the tab just looks empty, with nothing to diagnose from.
                PluginLog.Error(ex);
                Content = ErrorPanel(ex);
            }
        }
        public override void Close() { panel?.Dispose(); panel = null; }

        private static FrameworkElement ErrorPanel(Exception ex)
        {
            var box = new TextBox
            {
                Text = "AI Search failed to load:\n\n" + ex + "\n\nCollector log: %ProgramData%\\MilestoneSearch\\logs\\collector.log",
                IsReadOnly = true, TextWrapping = TextWrapping.Wrap, AcceptsReturn = true,
                Background = new SolidColorBrush(Color.FromRgb(0x08, 0x0c, 0x14)), Foreground = Brushes.White,
                BorderThickness = new Thickness(0), FontFamily = new FontFamily("Consolas"), FontSize = 12, Padding = new Thickness(20),
                VerticalScrollBarVisibility = ScrollBarVisibility.Auto,
            };
            return box;
        }
    }

    internal sealed class NativeSearchPanel : Grid, IDisposable
    {
        private readonly TextBlock statusBar = new TextBlock { Foreground = Theme.B(Theme.Muted), FontSize = 12, Margin = new Thickness(0, 0, 0, 10) };
        private readonly TextBox input = new TextBox { Background = Brushes.Transparent, BorderThickness = new Thickness(0), Foreground = Theme.B(Theme.Ink),
            FontSize = 15, VerticalContentAlignment = VerticalAlignment.Center, CaretBrush = Theme.B(Theme.Ink) };
        private readonly TextBlock placeholder = new TextBlock { Text = "Hỏi về sự kiện… ví dụ: camera .62 mất kết nối tối qua", Foreground = Theme.B(Theme.Muted),
            FontSize = 15, VerticalAlignment = VerticalAlignment.Center, Margin = new Thickness(4, 0, 0, 0), IsHitTestVisible = false };
        private readonly Button micButton = Icon(""); // mic glyph (Segoe MDL2 Assets)
        private readonly Button photoButton = Icon(""); // camera glyph
        private readonly Button sendButton = Icon("", accent: true); // send glyph
        private readonly StackPanel examples = new StackPanel { Orientation = Orientation.Horizontal, HorizontalAlignment = HorizontalAlignment.Center, Margin = new Thickness(0, 14, 0, 0) };
        private readonly WrapPanel wrapExamples = new WrapPanel { HorizontalAlignment = HorizontalAlignment.Center, Margin = new Thickness(0, 14, 0, 0) };
        private readonly TextBlock heroTitle = new TextBlock { Text = "Tìm mọi thứ\nchỉ bằng text hoặc voice.", Foreground = Theme.B(Theme.Ink), FontSize = 30, FontWeight = FontWeights.Bold,
            TextAlignment = TextAlignment.Center, Margin = new Thickness(0, 0, 0, 10) };
        private readonly TextBlock summary = new TextBlock { Foreground = Theme.B(Theme.Ink), FontSize = 15, FontWeight = FontWeights.SemiBold, Margin = new Thickness(0, 20, 0, 10) };
        private readonly WrapPanel chips = new WrapPanel { Margin = new Thickness(0, 0, 0, 12) };
        private readonly StackPanel results = new StackPanel();
        private readonly ScrollViewer resultsScroll;
        private readonly StackPanel heroPanel;
        private readonly Grid searchColumn;
        private readonly Border detail;
        private readonly StackPanel detailBody = new StackPanel();

        private BackendSession session;
        private JObject capabilities = new JObject();
        private JObject similarity = new JObject();
        private string lastQuery = "";
        private int offset;
        private const int Page = 50;
        private MicRecorder recorder;
        private DispatcherTimer micTimeout;
        private readonly string[] exampleQueries = { "Chuyển động hôm nay", "Camera mất kết nối tuần này", "Nhận diện khuôn mặt hôm qua", "Xâm nhập sau 22h tối qua", "Cảnh báo 7 ngày qua" };

        public NativeSearchPanel()
        {
            Background = Theme.B(Theme.Bg);
            ColumnDefinitions.Add(new ColumnDefinition());
            ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });

            searchColumn = new Grid();
            searchColumn.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
            searchColumn.RowDefinitions.Add(new RowDefinition());
            SetColumn(searchColumn, 0);
            Children.Add(searchColumn);

            var topBar = new DockPanel { Margin = new Thickness(20, 14, 20, 0) };
            DockPanel.SetDock(statusBar, Dock.Top);
            topBar.Children.Add(statusBar);
            SetRow(topBar, 0);
            searchColumn.Children.Add(topBar);

            var scroll = new ScrollViewer { VerticalScrollBarVisibility = ScrollBarVisibility.Auto, Padding = new Thickness(24, 10, 24, 30) };
            SetRow(scroll, 1);
            searchColumn.Children.Add(scroll);
            var content = new StackPanel { MaxWidth = 760, HorizontalAlignment = HorizontalAlignment.Center };
            scroll.Content = content;

            heroPanel = new StackPanel { Margin = new Thickness(0, 40, 0, 0) };
            var badge = new Border { Background = Theme.Grad(), CornerRadius = new CornerRadius(999), Padding = new Thickness(12, 5, 12, 5),
                HorizontalAlignment = HorizontalAlignment.Center, Margin = new Thickness(0, 0, 0, 16) };
            badge.Child = new TextBlock { Text = "SEARCH ENGINE · AI", Foreground = Brushes.White, FontSize = 10, FontWeight = FontWeights.Bold };
            heroPanel.Children.Add(badge);
            heroPanel.Children.Add(heroTitle);
            heroPanel.Children.Add(new TextBlock { Text = "Tìm sự kiện, khuôn mặt, đặc điểm người và xe bằng câu hỏi tiếng Việt, giọng nói, hoặc một tấm ảnh.",
                Foreground = Theme.B(Theme.Muted), FontSize = 14, TextAlignment = TextAlignment.Center, TextWrapping = TextWrapping.Wrap, MaxWidth = 520, HorizontalAlignment = HorizontalAlignment.Center });
            content.Children.Add(heroPanel);

            var askBorder = new Border { Background = Theme.B(Theme.SurfaceStrong), BorderBrush = Theme.Grad(), BorderThickness = new Thickness(1.5),
                CornerRadius = new CornerRadius(26), Padding = new Thickness(20, 6, 6, 6), Margin = new Thickness(0, 22, 0, 0) };
            var askRow = new DockPanel();
            var inputGrid = new Grid { VerticalAlignment = VerticalAlignment.Center };
            inputGrid.Children.Add(placeholder); inputGrid.Children.Add(input);
            input.TextChanged += (s, e) => placeholder.Visibility = string.IsNullOrEmpty(input.Text) ? Visibility.Visible : Visibility.Collapsed;
            input.KeyDown += (s, e) => { if (e.Key == Key.Enter) { e.Handled = true; _ = RunSearch(input.Text.Trim()); } };
            DockPanel.SetDock(photoButton, Dock.Right); DockPanel.SetDock(micButton, Dock.Right); DockPanel.SetDock(sendButton, Dock.Right);
            askRow.Children.Add(sendButton); askRow.Children.Add(micButton); askRow.Children.Add(photoButton); askRow.Children.Add(inputGrid);
            askBorder.Child = askRow;
            content.Children.Add(askBorder);
            sendButton.Click += (s, e) => _ = RunSearch(input.Text.Trim());
            micButton.Click += (s, e) => ToggleMic();
            photoButton.Click += (s, e) => PickPhoto();

            wrapExamples.Visibility = Visibility.Visible;
            foreach (var q in exampleQueries)
            {
                var b = Pill(q); b.Click += (s, e) => { input.Text = q; _ = RunSearch(q); }; wrapExamples.Children.Add(b);
            }
            content.Children.Add(wrapExamples);

            content.Children.Add(summary);
            content.Children.Add(chips);
            content.Children.Add(results);

            detail = new Border { Width = 0, Background = Theme.B(Theme.SurfaceStrong), BorderBrush = Theme.B(Theme.Line), BorderThickness = new Thickness(1, 0, 0, 0), ClipToBounds = true };
            SetColumn(detail, 1);
            Children.Add(detail);
            var detailScroll = new ScrollViewer { VerticalScrollBarVisibility = ScrollBarVisibility.Auto, Padding = new Thickness(22) };
            detailScroll.Content = detailBody;
            var detailHost = new Grid();
            var closeBtn = Icon("");
            closeBtn.HorizontalAlignment = HorizontalAlignment.Right; closeBtn.VerticalAlignment = VerticalAlignment.Top; closeBtn.Margin = new Thickness(8);
            closeBtn.Click += (s, e) => CloseDetail();
            detailHost.Children.Add(detailScroll); detailHost.Children.Add(closeBtn);
            detail.Child = detailHost;

            resultsScroll = scroll;
        }

        private static Button Pill(string text) => new Button { Content = text, Foreground = Theme.B(Theme.Muted), Background = Theme.B(Theme.Surface),
            BorderBrush = Theme.B(Theme.Line), BorderThickness = new Thickness(1), Padding = new Thickness(14, 7, 14, 7), Margin = new Thickness(4),
            FontSize = 12, Cursor = Cursors.Hand, Template = FlatTemplate(999) };

        private static ControlTemplate FlatTemplate(double radius)
        {
            var factory = new System.Windows.FrameworkElementFactory(typeof(Border));
            factory.SetValue(Border.BackgroundProperty, new TemplateBindingExtension(Control.BackgroundProperty));
            factory.SetValue(Border.BorderBrushProperty, new TemplateBindingExtension(Control.BorderBrushProperty));
            factory.SetValue(Border.BorderThicknessProperty, new TemplateBindingExtension(Control.BorderThicknessProperty));
            factory.SetValue(Border.CornerRadiusProperty, new CornerRadius(radius));
            var content = new System.Windows.FrameworkElementFactory(typeof(ContentPresenter));
            content.SetValue(ContentPresenter.HorizontalAlignmentProperty, HorizontalAlignment.Center);
            content.SetValue(ContentPresenter.VerticalAlignmentProperty, VerticalAlignment.Center);
            factory.AppendChild(content);
            return new ControlTemplate(typeof(Button)) { VisualTree = factory };
        }

        private static Button Icon(string glyph, bool accent = false) => new Button
        {
            Content = new TextBlock { Text = glyph, FontFamily = new FontFamily("Segoe MDL2 Assets"), FontSize = 17,
                Foreground = accent ? Brushes.White : Theme.B(Theme.Muted), HorizontalAlignment = HorizontalAlignment.Center, VerticalAlignment = VerticalAlignment.Center },
            Width = 40, Height = 40, Margin = new Thickness(4), Cursor = Cursors.Hand, Background = accent ? Theme.Grad() : Brushes.Transparent,
            BorderThickness = new Thickness(0), Template = FlatTemplate(20),
        };

        public void Start()
        {
            statusBar.Text = "Đang kết nối…";
            _ = Connect();
        }

        private async Task Connect()
        {
            try
            {
                var settings = PluginSettings.Load();
                session = new BackendSession(settings.BackendUrl);
                var login = VideoOS.Platform.Login.LoginSettingsCache.GetLoginSettings(EnvironmentManager.Instance.MasterSite);
                string milestoneToken = login?.IdentityTokenCache?.Token;
                if (string.IsNullOrEmpty(milestoneToken)) throw new InvalidOperationException("Chưa đăng nhập Milestone.");
                var me = await session.SignIn(milestoneToken);
                capabilities = await session.Capabilities();
                micButton.Visibility = (bool)(capabilities["voice"] ?? false) ? Visibility.Visible : Visibility.Collapsed;
                photoButton.Visibility = (bool)(capabilities["vision"] ?? false) || (bool)(capabilities["activeguard"] ?? false) ? Visibility.Visible : Visibility.Collapsed;
                statusBar.Text = $"Đã kết nối · {(string)me["name"]}";
                input.Focus();
            }
            catch (Exception ex)
            {
                PluginLog.Error(ex);
                statusBar.Text = "Không kết nối được backend: " + ex.Message;
                statusBar.Foreground = Theme.B(Theme.Red);
            }
        }

        private async Task RunSearch(string text, bool more = false)
        {
            if (session == null || string.IsNullOrEmpty(session.Token)) { statusBar.Text = "Chưa kết nối backend."; return; }
            if (string.IsNullOrWhiteSpace(text) && !more) return;
            offset = more ? offset + Page : 0; lastQuery = text;
            wrapExamples.Visibility = Visibility.Collapsed; heroPanel.Visibility = Visibility.Collapsed;
            if (!more) { summary.Text = "Đang tìm…"; chips.Children.Clear(); results.Children.Clear(); }
            try
            {
                var data = await session.Ask(text, offset, Page, (int)TimeZoneInfo.Local.GetUtcOffset(DateTime.Now).TotalMinutes);
                Render(data, more);
            }
            catch (Exception ex) { summary.Text = ex.Message; PluginLog.Error(ex); }
        }

        private void Render(JObject data, bool more)
        {
            similarity = data["similarity"] as JObject ?? new JObject();
            if (!more)
            {
                int total = (int?)data["total"] ?? 0;
                var parts = new List<string> { $"Tìm thấy {total:N0} lần xuất hiện" };
                var people = (data["facets"]?["people"] as JArray)?.Take(3).Select(f => $"{(string)f["name"]} ({(int)f["count"]})").ToList();
                if (people != null && people.Count > 0) parts.Add(string.Join(", ", people));
                else
                {
                    var top = (data["facets"]?["event_types"] as JArray)?.Take(3).Select(f => $"{Labels.Event((string)f["name"])} ({(int)f["count"]})").ToList();
                    if (top != null && top.Count > 0) parts.Add(string.Join(", ", top));
                }
                int cams = (data["facets"]?["sources"] as JArray)?.Count ?? 0;
                if (cams > 0) parts.Add($"{(cams >= 8 ? "8+" : cams.ToString())} camera/thiết bị");
                summary.Text = total > 0 ? string.Join(" · ", parts) : ((string)data["note"] ?? "Không có sự kiện phù hợp. Thử khoảng thời gian rộng hơn hoặc bớt điều kiện.");
                chips.Children.Clear();
                foreach (var c in (data["understood"] as JArray) ?? new JArray())
                    chips.Children.Add(Chip((string)c["label"], (string)c["type"] == "time" ? Theme.Amber : Theme.Accent));
                if (data["described"] != null) chips.Children.Insert(0, Chip("Ảnh: " + (string)data["described"], Theme.Muted));
                if (data["ignored_words"] != null) chips.Children.Add(Chip($"Bỏ qua “{(string)data["ignored_words"]}” vì không khớp sự kiện nào", Theme.Muted));
            }
            foreach (var item in (data["items"] as JArray) ?? new JArray()) results.Children.Add(Row((JObject)item));
        }

        private Border Chip(string text, Color color) => new Border { Background = new SolidColorBrush(Color.FromArgb(0x28, color.R, color.G, color.B)),
            BorderBrush = new SolidColorBrush(Color.FromArgb(0x50, color.R, color.G, color.B)), BorderThickness = new Thickness(1), CornerRadius = new CornerRadius(999),
            Padding = new Thickness(10, 4, 10, 4), Margin = new Thickness(0, 0, 6, 6),
            Child = new TextBlock { Text = text, Foreground = Theme.B(color == Theme.Muted ? Theme.Muted : Theme.Ink), FontSize = 11, FontWeight = FontWeights.SemiBold } };

        private Border Row(JObject item)
        {
            var occurred = DateTimeOffset.Parse((string)item["occurred_at"], CultureInfo.InvariantCulture).ToLocalTime();
            var border = new Border { Background = Theme.B(Theme.Surface), BorderBrush = Theme.B(Theme.Line), BorderThickness = new Thickness(1),
                CornerRadius = new CornerRadius(14), Padding = new Thickness(16, 12, 16, 12), Margin = new Thickness(0, 0, 0, 8), Cursor = Cursors.Hand };
            var row = new DockPanel();

            var time = new StackPanel { Width = 64, Margin = new Thickness(0, 0, 12, 0) };
            time.Children.Add(new TextBlock { Text = occurred.ToString("HH:mm", CultureInfo.InvariantCulture), Foreground = Theme.B(Theme.Ink), FontSize = 15, FontWeight = FontWeights.Bold });
            time.Children.Add(new TextBlock { Text = occurred.ToString("dd/MM", CultureInfo.InvariantCulture), Foreground = Theme.B(Theme.Muted), FontSize = 10 });
            DockPanel.SetDock(time, Dock.Left); row.Children.Add(time);

            bool hasImage = (bool?)item["has_image"] == true || (bool?)item["episode"]?["image"] == true;
            if (hasImage)
            {
                var img = new System.Windows.Controls.Image { Width = 52, Height = 52, Stretch = Stretch.UniformToFill, Margin = new Thickness(0, 0, 12, 0) };
                var clip = new Border { Width = 52, Height = 52, CornerRadius = new CornerRadius(10), Background = Theme.B(Theme.SurfaceStrong), Margin = new Thickness(0, 0, 12, 0) };
                DockPanel.SetDock(clip, Dock.Left); row.Children.Add(clip);
                LoadImage((string)item["key"], img);
                clip.Child = img;
            }

            var body = new StackPanel();
            var titleRow = new StackPanel { Orientation = Orientation.Horizontal };
            bool isAlarm = (string)item["kind"] == "alarm" || item["episode"]?["alarms"] != null;
            if (isAlarm) titleRow.Children.Add(new Border { Background = Theme.B(Theme.Red), CornerRadius = new CornerRadius(5), Padding = new Thickness(6, 1, 6, 1), Margin = new Thickness(0, 0, 8, 0),
                Child = new TextBlock { Text = "ALARM", Foreground = Brushes.White, FontSize = 9, FontWeight = FontWeights.Bold } });
            string title = Labels.Event((string)item["event_label"] ?? (string)item["event_name"] ?? (string)item["message"]) ?? (string)item["event_type"];
            titleRow.Children.Add(new TextBlock { Text = title, Foreground = Theme.B(Theme.Ink), FontWeight = FontWeights.Bold, FontSize = 13 });
            body.Children.Add(titleRow);
            string eventType = (string)item["event_type"] ?? "";
            if (eventType.StartsWith("activeguard:") && item["description"] != null)
                body.Children.Add(new TextBlock { Text = (string)item["description"], Foreground = Theme.B(Theme.Muted), FontSize = 12, Margin = new Thickness(0, 2, 0, 0), TextWrapping = TextWrapping.Wrap });
            body.Children.Add(new TextBlock { Text = (string)item["source_name"] ?? (string)item["source_id"], Foreground = Theme.B(Theme.Muted), FontSize = 12, Margin = new Thickness(0, 2, 0, 0) });

            var tags = new WrapPanel { Margin = new Thickness(0, 5, 0, 0) };
            string key = (string)item["key"];
            if (similarity[key] != null) tags.Children.Add(TagChip($"Giống {Math.Round((double)similarity[key])}%", Theme.Accent));
            foreach (var name in (item["facts"]?["persons"] as JArray) ?? new JArray()) tags.Children.Add(TagChip((string)name, Theme.Accent));
            foreach (var name in ((item["facts"]?["plates"] as JArray) ?? new JArray()).Concat((item["facts"]?["watchlists"] as JArray) ?? new JArray())) tags.Children.Add(TagChip((string)name, Theme.Accent));
            if ((string)item["facts"]?["identity_status"] == "unknown") tags.Children.Add(TagChip("Người lạ", Theme.Muted));
            foreach (var a in (item["facts"]?["action"] as JArray) ?? new JArray()) tags.Children.Add(TagChip((string)a, Theme.Muted));
            int episodeCount = (int?)item["episode"]?["count"] ?? 0;
            if (episodeCount > 1) tags.Children.Add(TagChip($"×{episodeCount}", Theme.Amber));
            if (tags.Children.Count > 0) body.Children.Add(tags);
            row.Children.Add(body);
            border.Child = row;
            border.MouseLeftButtonUp += (s, e) => ShowDetail(item);
            return border;
        }

        private Border TagChip(string text, Color color) => new Border { Background = new SolidColorBrush(Color.FromArgb(0x22, color.R, color.G, color.B)),
            BorderBrush = new SolidColorBrush(Color.FromArgb(0x40, color.R, color.G, color.B)), BorderThickness = new Thickness(1), CornerRadius = new CornerRadius(999),
            Padding = new Thickness(8, 1, 8, 1), Margin = new Thickness(0, 0, 4, 0),
            Child = new TextBlock { Text = text, Foreground = Theme.B(Theme.Ink), FontSize = 10, FontWeight = FontWeights.SemiBold } };

        private async void LoadImage(string key, System.Windows.Controls.Image target)
        {
            try
            {
                byte[] bytes = await session.Image(key);
                if (bytes == null) return;
                var bitmap = new BitmapImage();
                using (var stream = new MemoryStream(bytes))
                {
                    bitmap.BeginInit(); bitmap.CacheOption = BitmapCacheOption.OnLoad; bitmap.StreamSource = stream; bitmap.EndInit();
                }
                bitmap.Freeze();
                target.Source = bitmap;
            }
            catch (Exception ex) { PluginLog.Error(ex); }
        }

        private void ShowDetail(JObject item)
        {
            detailBody.Children.Clear();
            bool isAlarm = (string)item["kind"] == "alarm";
            detailBody.Children.Add(new TextBlock { Text = isAlarm ? "ALARM" : "SỰ KIỆN", Foreground = Theme.B(Theme.Accent), FontSize = 10, FontWeight = FontWeights.Bold, Margin = new Thickness(0, 0, 0, 6) });
            string title = Labels.Event((string)item["event_label"] ?? (string)item["event_name"] ?? (string)item["message"]) ?? (string)item["event_type"];
            detailBody.Children.Add(new TextBlock { Text = title, Foreground = Theme.B(Theme.Ink), FontSize = 19, FontWeight = FontWeights.Bold, TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 0, 0, 12) });

            bool hasImage = (bool?)item["has_image"] == true || (bool?)item["episode"]?["image"] == true;
            if (hasImage)
            {
                var img = new System.Windows.Controls.Image { Stretch = Stretch.UniformToFill, Height = 220, Margin = new Thickness(0, 0, 0, 12) };
                var clip = new Border { CornerRadius = new CornerRadius(10), Background = Theme.B(Theme.Surface), Child = img, ClipToBounds = true };
                detailBody.Children.Add(clip);
                LoadImage((string)item["key"], img);
            }

            var fields = new (string label, string value)[]
            {
                ("Thời điểm", When(item["occurred_at"])),
                ("Camera / thiết bị", (string)item["source_name"] ?? (string)item["source_id"]),
                ("Tên trong Milestone", (string)item["event_name"]),
                ("Nội dung", (string)item["message"] != (string)item["event_name"] ? (string)item["message"] : null),
                ("Người", string.Join(", ", (item["facts"]?["persons"] as JArray) ?? new JArray())),
                ("Hành động", string.Join(", ", (item["facts"]?["action"] as JArray) ?? new JArray())),
                ("Nhận diện", (string)item["facts"]?["identity_status"] == "unknown" ? "Người lạ" : (string)item["facts"]?["identity_status"] == "known" ? "Người đã đăng ký" : null),
                ("Đặc điểm", ((string)item["event_type"] ?? "").StartsWith("activeguard:") ? (string)item["description"] : null),
                ("Số lần lặp", (int?)item["episode"]?["count"] > 1 ? $"{(int)item["episode"]["count"]} lần, {When(item["episode"]["first"])} → {When(item["episode"]["last"])}" : null),
                ("Mức ưu tiên", (string)item["priority"]), ("Trạng thái", (string)item["state"]), ("Địa điểm", (string)item["location"]), ("Mô tả", (string)item["description"]),
            };
            var grid = new Grid();
            grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(110) });
            grid.ColumnDefinitions.Add(new ColumnDefinition());
            int r = 0;
            foreach (var (label, value) in fields)
            {
                if (string.IsNullOrEmpty(value)) continue;
                grid.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
                var l = new TextBlock { Text = label, Foreground = Theme.B(Theme.Muted), FontSize = 11, Margin = new Thickness(0, 0, 8, 8) };
                var v = new TextBlock { Text = value, Foreground = Theme.B(Theme.Ink), FontSize = 12, TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 0, 0, 8) };
                SetRow(l, r); SetRow(v, r); SetColumn(l, 0); SetColumn(v, 1);
                grid.Children.Add(l); grid.Children.Add(v); r++;
            }
            detailBody.Children.Add(grid);

            var persons = (item["facts"]?["persons"] as JArray) ?? new JArray();
            if (persons.Count > 0)
            {
                var related = new WrapPanel { Margin = new Thickness(0, 10, 0, 0) };
                foreach (var name in persons)
                {
                    var b = Pill($"Các lần khác của {(string)name}"); b.Click += (s, e) => { CloseDetail(); input.Text = (string)name; _ = RunSearch((string)name); };
                    related.Children.Add(b);
                }
                detailBody.Children.Add(related);
            }

            var cameraId = (string)item["camera_id"];
            if (!string.IsNullOrEmpty(cameraId))
            {
                var play = new Button { Content = "▶  Xem video tại thời điểm này", Foreground = Brushes.White, Background = Theme.Grad(), BorderThickness = new Thickness(0),
                    Padding = new Thickness(14, 10, 14, 10), Margin = new Thickness(0, 16, 0, 0), HorizontalAlignment = HorizontalAlignment.Stretch, Cursor = Cursors.Hand, Template = FlatTemplate(10) };
                play.Click += (s, e) => Playback(cameraId, (string)item["occurred_at"]);
                detailBody.Children.Add(play);
            }

            var raw = new Expander { Header = "Dữ liệu gốc", Foreground = Theme.B(Theme.Muted), Margin = new Thickness(0, 16, 0, 0),
                Content = new TextBox { Text = item["payload"]?.ToString(Formatting.Indented) ?? "", IsReadOnly = true, TextWrapping = TextWrapping.Wrap,
                    Background = Theme.B(Theme.Bg), Foreground = Theme.B(Theme.Muted), BorderThickness = new Thickness(0), FontFamily = new FontFamily("Consolas"), FontSize = 10, Margin = new Thickness(0, 8, 0, 0) } };
            detailBody.Children.Add(raw);

            detail.Width = 400;
        }

        private void Playback(string cameraId, string occurredAt)
        {
            try
            {
                var camera = Configuration.Instance.GetItem(Guid.Parse(cameraId), VideoOS.Platform.Kind.Camera);
                if (camera == null) throw new InvalidOperationException("Camera is unavailable to the current Milestone user.");
                var time = DateTimeOffset.Parse(occurredAt, CultureInfo.InvariantCulture);
                EnvironmentManager.Instance.SendMessage(new Message(MessageId.SmartClient.ShowCamerasInFloatingWindowCommand,
                    new ShowCamerasInFloatingWindowData { Cameras = new[] { camera }, Mode = Mode.ClientPlayback, BrowseTime = time.UtcDateTime }));
            }
            catch (Exception ex) { statusBar.Text = "Playback: " + ex.Message; PluginLog.Error(ex); }
        }

        private void CloseDetail() => detail.Width = 0;

        private static string When(JToken value)
        {
            if (value == null) return null;
            return DateTimeOffset.Parse((string)value, CultureInfo.InvariantCulture).ToLocalTime().ToString("HH:mm:ss dd/MM/yyyy", CultureInfo.InvariantCulture);
        }

        // ---------- voice ----------
        private void ToggleMic()
        {
            if (recorder != null && recorder.Recording) { StopMic(); return; }
            try
            {
                recorder = new MicRecorder();
                recorder.Start();
                micButton.Background = Theme.B(Theme.Red);
                statusBar.Text = "Đang nghe… bấm micro lần nữa để dừng"; statusBar.Foreground = Theme.B(Theme.Accent);
                micTimeout = new DispatcherTimer { Interval = TimeSpan.FromSeconds(30) };
                micTimeout.Tick += (s, e) => StopMic();
                micTimeout.Start();
            }
            catch (Exception ex) { statusBar.Text = "Không dùng được micro: " + ex.Message; PluginLog.Error(ex); }
        }

        private async void StopMic()
        {
            micTimeout?.Stop(); micTimeout = null;
            micButton.Background = Brushes.Transparent;
            if (recorder == null) return;
            byte[] wav;
            try { wav = recorder.Stop(); } finally { recorder.Dispose(); recorder = null; }
            statusBar.Text = "Đang chuyển giọng nói thành chữ…";
            try
            {
                string transcript = await session.Transcribe(wav);
                statusBar.Text = ""; statusBar.Foreground = Theme.B(Theme.Muted);
                input.Text = transcript;
                if (!string.IsNullOrWhiteSpace(transcript)) _ = RunSearch(transcript.Trim());
            }
            catch (Exception ex) { statusBar.Text = ex.Message; PluginLog.Error(ex); }
        }

        // ---------- photo search ----------
        private void PickPhoto()
        {
            var dialog = new System.Windows.Forms.OpenFileDialog { Filter = "Ảnh|*.jpg;*.jpeg;*.png;*.bmp;*.webp", Title = "Chọn ảnh để tìm" };
            if (dialog.ShowDialog() != System.Windows.Forms.DialogResult.OK) return;
            _ = RunPhotoSearch(dialog.FileName);
        }

        private async Task RunPhotoSearch(string path)
        {
            wrapExamples.Visibility = Visibility.Collapsed; heroPanel.Visibility = Visibility.Collapsed;
            summary.Text = "Đang phân tích ảnh…"; chips.Children.Clear(); results.Children.Clear();
            try
            {
                byte[] bytes = File.ReadAllBytes(path);
                var data = await session.AskImage(bytes, Path.GetFileName(path), (int)TimeZoneInfo.Local.GetUtcOffset(DateTime.Now).TotalMinutes);
                offset = 0; lastQuery = (string)data["described"];
                input.Text = lastQuery;
                Render(data, false);
            }
            catch (Exception ex) { summary.Text = ex.Message; PluginLog.Error(ex); }
        }

        public void Dispose() { recorder?.Dispose(); micTimeout?.Stop(); session?.Dispose(); }
    }
}
