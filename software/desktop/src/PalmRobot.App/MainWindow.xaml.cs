using System.Collections.Specialized;
using System.IO;
using System.Windows;
using System.Windows.Input;
using PalmRobot.App.Services;
using PalmRobot.App.ViewModels;

namespace PalmRobot.App;

public partial class MainWindow : Window
{
    private readonly MainViewModel _vm;

    public MainWindow()
    {
        InitializeComponent();
        _vm = (MainViewModel)DataContext;

        // 日志面板：追加 + 自动滚动 + 上限 2000
        LogService.Appended += (level, message) => Dispatcher.BeginInvoke(() =>
        {
            var tag = level switch
            {
                LogLevel.Warn => "[!] ",
                LogLevel.Error => "[X] ",
                LogLevel.Info => "[i] ",
                _ => "    ",
            };
            _vm.LogLines.Add($"{DateTime.Now:HH:mm:ss.fff} {tag}{message}");
            while (_vm.LogLines.Count > 2000) _vm.LogLines.RemoveAt(0);
            if (LogList.Items.Count > 0)
                LogList.ScrollIntoView(LogList.Items[^1]);
        });

        // 命令行自动滚动
        ((INotifyCollectionChanged)_vm.ConsoleLines).CollectionChanged += (_, _) =>
            Dispatcher.BeginInvoke(() =>
            {
                if (ConsoleList.Items.Count > 0)
                    ConsoleList.ScrollIntoView(ConsoleList.Items[^1]);
            });

        Loaded += async (_, _) =>
        {
            await InitPreview3DAsync();
            InitSimVideoView();
        };
    }

    // —— 仿真画面（MuJoCo 离屏渲染 JPEG 流，sim_server.py 8556） ——

    private void InitSimVideoView()
    {
        _vm.SimFrameReceived += jpeg => Dispatcher.BeginInvoke(() =>
        {
            try
            {
                var img = new System.Windows.Media.Imaging.BitmapImage();
                using var ms = new MemoryStream(jpeg);
                img.BeginInit();
                img.CacheOption = System.Windows.Media.Imaging.BitmapCacheOption.OnLoad;
                img.StreamSource = ms;
                img.EndInit();
                img.Freeze();   // 跨线程可渲染
                SimVideoImage.Source = img;
                SimVideoHint.Text = string.Empty;
            }
            catch { /* 坏帧丢弃 */ }
        });
        _vm.WatchSimVideoStatus(msg => Dispatcher.BeginInvoke(() => SimVideoHint.Text = msg));
        Closed += (_, _) => _vm.ShutdownSimServer();
    }

    // —— 3D 预览（WebView2 内嵌 web/index.html） ——

    private async Task InitPreview3DAsync()
    {
        var index = FindWebIndex();
        if (index is null)
        {
            PreviewHint.Text = "未找到 web/index.html";
            return;
        }
        try
        {
            await Preview3D.EnsureCoreWebView2Async();
            Preview3D.CoreWebView2.WebMessageReceived += (_, args) =>
            {
                // 页面就绪后推当前角度
                if (args.TryGetWebMessageAsString().Contains("preview-ready"))
                    PushAnglesToPreview(Enumerable.Range(0, 8)
                        .Select(i => _vm.ServoChannels[i].Angle).ToArray());
            };
            Preview3D.CoreWebView2.Navigate(new Uri(index).AbsoluteUri);
            _vm.FrameSent += PushAnglesToPreview;
            _vm.MotionRequested += PushMotionToPreview;
            _vm.MotionJsonGenerated += PushMotionJsonToPreview;
            PreviewHint.Text = string.Empty;
        }
        catch (Exception ex)
        {
            PreviewHint.Text = $"3D 预览不可用: {ex.Message}（需安装 WebView2 Runtime）";
        }
    }

    private void PushAnglesToPreview(int[] angles)
    {
        if (Preview3D.CoreWebView2 is null) return;
        Preview3D.CoreWebView2.PostWebMessageAsJson(
            System.Text.Json.JsonSerializer.Serialize(new { type = "angles", value = angles }));
    }

    /// <summary>动作播放联动：wave/dance → 3D 预览同步播放；STOP → 停止播放。</summary>
    private void PushMotionToPreview(string? name)
    {
        if (Preview3D.CoreWebView2 is null) return;
        var msg = name is null
            ? """{"type":"stop"}"""
            : System.Text.Json.JsonSerializer.Serialize(new { type = "motion", name });
        Preview3D.CoreWebView2.PostWebMessageAsJson(msg);
    }

    /// <summary>视频转动作产物：整包 JSON 推给 3D 预览播放。</summary>
    private void PushMotionJsonToPreview(string motionJson)
    {
        if (Preview3D.CoreWebView2 is null) return;
        try
        {
            using var doc = System.Text.Json.JsonDocument.Parse(motionJson);
            var wrapper = System.Text.Json.JsonSerializer.Serialize(
                new { type = "motion", data = doc.RootElement.Clone() });
            Preview3D.CoreWebView2.PostWebMessageAsJson(wrapper);
        }
        catch { /* 非法 JSON 丢弃 */ }
    }

    /// <summary>从 exe 目录向上找 software/web/index.html。</summary>
    private static string? FindWebIndex()
    {
        for (var d = new DirectoryInfo(AppContext.BaseDirectory); d is not null; d = d.Parent)
        {
            var cand = Path.Combine(d.FullName, "web", "index.html");
            if (File.Exists(cand)) return cand;
        }
        return null;
    }

    private void ConsoleInput_KeyDown(object sender, KeyEventArgs e)
    {
        if (e.Key == Key.Enter)
        {
            _vm.SendConsoleCommand.Execute(null);
            e.Handled = true;
        }
    }
}
