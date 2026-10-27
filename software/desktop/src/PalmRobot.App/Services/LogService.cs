using System.IO;
using Serilog;
using Serilog.Core;
using Serilog.Events;

namespace PalmRobot.App.Services;

public enum LogLevel { Debug, Info, Warn, Error, Raw }

/// <summary>日志门面：Serilog 文件 + UI 事件。</summary>
public static class LogService
{
    private static Logger? _fileLogger;

    /// <summary>UI 日志面板订阅此事件（需自行调度到 UI 线程）。</summary>
    public static event Action<LogLevel, string>? Appended;

    public static void Initialize(string logDirectory)
    {
        _fileLogger = new LoggerConfiguration()
            .MinimumLevel.Debug()
            .WriteTo.File(
                Path.Combine(logDirectory, "palmrobot-.log"),
                rollingInterval: RollingInterval.Day,
                retainedFileCountLimit: 14,
                outputTemplate: "{Timestamp:HH:mm:ss.fff} [{Level:u3}] {Message:lj}{NewLine}{Exception}")
            .CreateLogger();
    }

    public static void Write(LogLevel level, string message)
    {
        Appended?.Invoke(level, message);
        if (_fileLogger is null) return;
        _fileLogger.Write(level switch
        {
            LogLevel.Debug => LogEventLevel.Debug,
            LogLevel.Info => LogEventLevel.Information,
            LogLevel.Warn => LogEventLevel.Warning,
            LogLevel.Error => LogEventLevel.Error,
            _ => LogEventLevel.Debug,
        }, message);
    }

    public static void Info(string message) => Write(LogLevel.Info, message);
    public static void Warn(string message) => Write(LogLevel.Warn, message);
    public static void Error(string message) => Write(LogLevel.Error, message);
    public static void Debug(string message) => Write(LogLevel.Debug, message);

    public static void Shutdown() => _fileLogger?.Dispose();
}
