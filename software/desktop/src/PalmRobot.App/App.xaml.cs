using System.IO;
using System.Windows;
using PalmRobot.App.Services;

namespace PalmRobot.App;

public partial class App : Application
{
    protected override void OnStartup(StartupEventArgs e)
    {
        base.OnStartup(e);
        var logDir = Path.Combine(AppContext.BaseDirectory, "logs");
        Directory.CreateDirectory(logDir);
        LogService.Initialize(logDir);
        LogService.Info("上位机启动");
        DispatcherUnhandledException += (_, args) =>
        {
            LogService.Error($"未处理异常: {args.Exception.Message}\n{args.Exception.StackTrace}");
            args.Handled = true;
        };
    }

    protected override void OnExit(ExitEventArgs e)
    {
        LogService.Info("上位机退出");
        LogService.Shutdown();
        base.OnExit(e);
    }
}
