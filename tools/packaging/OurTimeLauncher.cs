using System;
using System.Diagnostics;
using System.IO;
using System.Net;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Windows.Forms;

internal static class Program
{
    private const int Port = 8765;

    [STAThread]
    private static int Main(string[] args)
    {
        Application.EnableVisualStyles();
        try
        {
            string exe = Application.ExecutablePath;
            string root = Path.GetDirectoryName(exe);
            bool stop = Array.Exists(args, a => a == "--stop") ||
                        Path.GetFileNameWithoutExtension(exe).IndexOf("停止") >= 0;
            string appDir = Path.Combine(root, "App");
            string dataDir = Path.Combine(root, "Data");
            string pythonw = Path.Combine(appDir, "python", "pythonw.exe");
            string python = Path.Combine(appDir, "python", "python.exe");
            string appPy = Path.Combine(appDir, "app.py");
            if (!File.Exists(appPy) || (!File.Exists(pythonw) && !File.Exists(python)))
            {
                MessageBox.Show("没有找到 App 程序目录或内置 Python。请使用完整解压后的拾光相册文件夹。", "拾光相册");
                return 1;
            }
            Directory.CreateDirectory(dataDir);
            Directory.CreateDirectory(Path.Combine(dataDir, "thumbs"));
            Directory.CreateDirectory(Path.Combine(dataDir, "faces"));
            string url = "http://127.0.0.1:" + Port + "/";
            string dataKey = DataKey(dataDir);
            if (stop)
            {
                return StopServer(url, dataKey, appPy);
            }
            if (IsOurServer(url, dataKey))
            {
                Process.Start(url);
                return 0;
            }
            if (PortBusy())
            {
                MessageBox.Show("端口 " + Port + " 已被其他程序占用，拾光没有改用别的资料库。", "拾光相册");
                return 1;
            }
            var psi = new ProcessStartInfo();
            psi.FileName = File.Exists(pythonw) ? pythonw : python;
            psi.Arguments = "\"" + appPy + "\" --port " + Port;
            psi.WorkingDirectory = appDir;
            psi.UseShellExecute = false;
            psi.CreateNoWindow = true;
            psi.EnvironmentVariables["PHOTO_LIBRARY_DATA"] = dataDir;
            psi.EnvironmentVariables["PHOTO_LIBRARY_PORT"] = Port.ToString();
            psi.EnvironmentVariables["PHOTO_MODEL_ROOT"] = Path.Combine(appDir, "resources", "models");
            psi.EnvironmentVariables["PHOTO_GEO_ROOT"] = Path.Combine(appDir, "resources", "geo");
            psi.EnvironmentVariables["PHOTO_FACE_LABEL_DIR"] = Path.Combine(appDir, "web", "assets", "face-labels");
            psi.EnvironmentVariables["PHOTO_CHROMIUM"] = Path.Combine(appDir, "browsers", "chromium-1200", "chrome-win64", "chrome.exe");
            psi.EnvironmentVariables["PLAYWRIGHT_BROWSERS_PATH"] = Path.Combine(appDir, "browsers");
            psi.EnvironmentVariables["PYTHONHOME"] = Path.Combine(appDir, "python");
            psi.EnvironmentVariables["PYTHONPATH"] = appDir;
            psi.EnvironmentVariables["PYTHONUTF8"] = "1";
            psi.EnvironmentVariables["NO_ALBUMENTATIONS_UPDATE"] = "1";
            string log = Path.Combine(dataDir, "server.log");
            string err = Path.Combine(dataDir, "server-error.log");
            psi.RedirectStandardOutput = true;
            psi.RedirectStandardError = true;
            var proc = Process.Start(psi);
            if (proc == null)
            {
                MessageBox.Show("无法启动拾光后台。", "拾光相册");
                return 1;
            }
            Drain(proc.StandardOutput, log);
            Drain(proc.StandardError, err);
            for (int i = 0; i < 60; i++)
            {
                if (proc.HasExited) break;
                if (IsOurServer(url, dataKey))
                {
                    File.WriteAllText(Path.Combine(dataDir, "server.pid"), proc.Id.ToString(), Encoding.ASCII);
                    Process.Start(url);
                    return 0;
                }
                Thread.Sleep(500);
            }
            MessageBox.Show("启动失败，请查看 Data\\server-error.log", "拾光相册");
            return 1;
        }
        catch (Exception ex)
        {
            MessageBox.Show(ex.Message, "拾光启动失败");
            return 1;
        }
    }

    private static void Drain(StreamReader reader, string path)
    {
        var thread = new Thread(() =>
        {
            try
            {
                using (var writer = new StreamWriter(path, true, Encoding.UTF8))
                {
                    string line;
                    while ((line = reader.ReadLine()) != null)
                    {
                        writer.WriteLine(line);
                        writer.Flush();
                    }
                }
            }
            catch { }
        });
        thread.IsBackground = true;
        thread.Start();
    }

    private static string DataKey(string dataDir)
    {
        string normalized = Path.GetFullPath(dataDir).ToLowerInvariant();
        using (var sha = SHA256.Create())
        {
            byte[] hash = sha.ComputeHash(Encoding.UTF8.GetBytes(normalized));
            var sb = new StringBuilder(hash.Length * 2);
            foreach (byte b in hash) sb.Append(b.ToString("x2"));
            return sb.ToString();
        }
    }

    private static bool IsOurServer(string url, string dataKey)
    {
        try
        {
            var req = (HttpWebRequest)WebRequest.Create(url + "api/health");
            req.Timeout = 2000;
            req.ReadWriteTimeout = 2000;
            using (var resp = (HttpWebResponse)req.GetResponse())
            using (var reader = new StreamReader(resp.GetResponseStream(), Encoding.UTF8))
            {
                string body = reader.ReadToEnd();
                return body.IndexOf("\"ok\":true") >= 0 && body.IndexOf(dataKey) >= 0;
            }
        }
        catch { return false; }
    }

    private static bool PortBusy()
    {
        try
        {
            var req = (HttpWebRequest)WebRequest.Create("http://127.0.0.1:" + Port + "/api/health");
            req.Timeout = 800;
            using (var resp = req.GetResponse()) { return true; }
        }
        catch (WebException ex)
        {
            if (ex.Response != null) return true;
            return false;
        }
        catch { return false; }
    }

    private static int StopServer(string url, string dataKey, string appPy)
    {
        if (!IsOurServer(url, dataKey))
        {
            MessageBox.Show("没有正在运行的拾光，或当前端口不是这份资料库。", "拾光相册");
            return 1;
        }
        try
        {
            Post(url + "api/scan/pause");
            for (int i = 0; i < 40; i++)
            {
                Thread.Sleep(250);
            }
            Post(url + "api/shutdown");
            for (int i = 0; i < 40; i++)
            {
                if (!IsOurServer(url, dataKey)) return 0;
                Thread.Sleep(250);
            }
            MessageBox.Show("后台没有在安全点停住，未强制结束。", "拾光相册");
            return 1;
        }
        catch (Exception ex)
        {
            MessageBox.Show("停止失败：" + ex.Message, "拾光相册");
            return 1;
        }
    }

    private static void Post(string url)
    {
        var req = (HttpWebRequest)WebRequest.Create(url);
        req.Method = "POST";
        req.Timeout = 4000;
        req.ContentLength = 0;
        using (req.GetResponse()) { }
    }
}
