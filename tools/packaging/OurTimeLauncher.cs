using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Windows.Forms;

internal static class Program
{
    private const int DefaultPort = 8765;
    private static bool NoDialog;

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
            bool noBrowser = Array.Exists(args, a => a == "--no-browser") ||
                             Environment.GetEnvironmentVariable("PHOTO_NO_BROWSER") == "1";
            NoDialog = Environment.GetEnvironmentVariable("PHOTO_NO_DIALOG") == "1";
            int port = ResolvePort();
            string appDir = Path.Combine(root, "App");
            string dataDir = Environment.GetEnvironmentVariable("PHOTO_LIBRARY_DATA");
            if (String.IsNullOrWhiteSpace(dataDir)) dataDir = Path.Combine(root, "Data");
            dataDir = Path.GetFullPath(dataDir);
            string pythonw = Path.Combine(appDir, "python", "pythonw.exe");
            string python = Path.Combine(appDir, "python", "python.exe");
            string appPy = Path.Combine(appDir, "app.py");
            if (!File.Exists(appPy) || (!File.Exists(pythonw) && !File.Exists(python)))
            {
                ShowMessage("没有找到 App 程序目录或内置 Python。请使用完整解压后的拾光相册文件夹。", "拾光相册");
                return 1;
            }
            if (LooksLikeTempUnpack(root) && !NoDialog)
            {
                var ask = MessageBox.Show("当前像是在压缩包的临时目录里运行。请先解压到普通文件夹再打开，否则关闭后文件可能消失。\n\n仍要继续吗？", "拾光相册", MessageBoxButtons.YesNo, MessageBoxIcon.Warning);
                if (ask != DialogResult.Yes) return 1;
            }
            Directory.CreateDirectory(dataDir);
            Directory.CreateDirectory(Path.Combine(dataDir, "thumbs"));
            Directory.CreateDirectory(Path.Combine(dataDir, "faces"));
            string url = "http://127.0.0.1:" + port + "/";
            string dataKey = DataKey(dataDir);
            if (stop)
            {
                return StopServer(url, dataKey, appPy);
            }
            if (PortBusy(port))
            {
                if (IsOurServer(url, dataKey))
                {
                    if (!noBrowser) OpenBrowser(url + (IsEmptyLibrary(url) ? "#scan" : ""));
                    return 0;
                }
                ShowMessage("端口 " + port + " 已被其他程序实际占用。请关闭占用程序，或设置 PHOTO_LIBRARY_PORT 后再启动。", "拾光相册");
                return 1;
            }
            var psi = new ProcessStartInfo();
            psi.FileName = File.Exists(pythonw) ? pythonw : python;
            psi.Arguments = "\"" + appPy + "\" --port " + port;
            psi.WorkingDirectory = appDir;
            psi.UseShellExecute = false;
            psi.CreateNoWindow = true;
            psi.EnvironmentVariables["PHOTO_LIBRARY_DATA"] = dataDir;
            psi.EnvironmentVariables["PHOTO_LIBRARY_PORT"] = port.ToString();
            psi.EnvironmentVariables["PHOTO_MODEL_ROOT"] = Path.Combine(appDir, "resources", "models");
            psi.EnvironmentVariables["PHOTO_GEO_ROOT"] = Path.Combine(appDir, "resources", "geo");
            psi.EnvironmentVariables["PHOTO_FACE_LABEL_DIR"] = Path.Combine(appDir, "web", "assets", "face-labels");
            psi.EnvironmentVariables["PHOTO_CHROMIUM"] = Path.Combine(appDir, "browsers", "chromium-1200", "chrome-win64", "chrome.exe");
            psi.EnvironmentVariables["PLAYWRIGHT_BROWSERS_PATH"] = Path.Combine(appDir, "browsers");
            psi.EnvironmentVariables["PYTHONHOME"] = Path.Combine(appDir, "python");
            psi.EnvironmentVariables["PYTHONPATH"] = appDir;
            psi.EnvironmentVariables["PYTHONUTF8"] = "1";
            psi.EnvironmentVariables["NO_ALBUMENTATIONS_UPDATE"] = "1";
            psi.EnvironmentVariables["PHOTO_UI_IDLE_EXIT_SECONDS"] = noBrowser ? "0" : "8";
            string log = Path.Combine(dataDir, "server.log");
            string err = Path.Combine(dataDir, "server-error.log");
            psi.RedirectStandardOutput = true;
            psi.RedirectStandardError = true;
            var proc = Process.Start(psi);
            if (proc == null)
            {
                ShowMessage("无法启动拾光后台。", "拾光相册");
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
                    bool empty = IsEmptyLibrary(url);
                    if (empty && !noBrowser)
                    {
                        using (var form = new FirstRunForm())
                        {
                            if (form.ShowDialog() == DialogResult.OK && form.Roots.Count > 0)
                                StartScan(url, form.Roots, FaceModelReady(url));
                        }
                    }
                    if (!noBrowser) OpenBrowser(url + (empty ? "#scan" : ""));
                    return 0;
                }
                Thread.Sleep(500);
            }
            ShowMessage("启动失败，请查看 Data\\server-error.log", "拾光相册");
            return 1;
        }
        catch (Exception ex)
        {
            ShowMessage(ex.Message, "拾光启动失败");
            return 1;
        }
    }

    private static void ShowMessage(string text, string caption)
    {
        if (!NoDialog) MessageBox.Show(text, caption);
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

    private static int ResolvePort()
    {
        int port;
        string raw = Environment.GetEnvironmentVariable("PHOTO_LIBRARY_PORT");
        if (!String.IsNullOrWhiteSpace(raw) && Int32.TryParse(raw, out port) &&
            port >= 1 && port <= 65535)
            return port;
        return DefaultPort;
    }

    private static bool PortBusy(int port)
    {
        TcpClient client = new TcpClient();
        try
        {
            IAsyncResult result = client.BeginConnect(IPAddress.Loopback, port, null, null);
            bool connected = result.AsyncWaitHandle.WaitOne(800);
            if (!connected) return false;
            client.EndConnect(result);
            return client.Connected;
        }
        catch { return false; }
        finally { client.Close(); }
    }

    private static int StopServer(string url, string dataKey, string appPy)
    {
        if (!IsOurServer(url, dataKey))
        {
            ShowMessage("没有正在运行的拾光，或当前端口不是这份资料库。", "拾光相册");
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
            ShowMessage("后台没有在安全点停住，未强制结束。", "拾光相册");
            return 1;
        }
        catch (Exception ex)
        {
            ShowMessage("停止失败：" + ex.Message, "拾光相册");
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

    private static bool LooksLikeTempUnpack(string root)
    {
        string t = root.ToLowerInvariant();
        return t.IndexOf("\\appdata\\local\\temp\\") >= 0
            || t.IndexOf("\\temp\\rar$") >= 0
            || t.IndexOf("\\temp\\7z") >= 0
            || t.IndexOf(".zip\\") >= 0
            || t.IndexOf(".rar\\") >= 0;
    }

    private static void OpenBrowser(string url)
    {
        try { Process.Start(new ProcessStartInfo(url) { UseShellExecute = true }); }
        catch { ShowMessage("浏览器没有自动打开。请手动访问：\n" + url, "拾光相册"); }
    }

    private static string HttpGet(string url)
    {
        var req = (HttpWebRequest)WebRequest.Create(url);
        req.Timeout = 3000;
        using (var resp = (HttpWebResponse)req.GetResponse())
        using (var reader = new StreamReader(resp.GetResponseStream(), Encoding.UTF8))
            return reader.ReadToEnd();
    }

    private static bool IsEmptyLibrary(string url)
    {
        try
        {
            var m = Regex.Match(HttpGet(url + "api/status"), "\"assets\"\\s*:\\s*(\\d+)");
            return m.Success && m.Groups[1].Value == "0";
        }
        catch { return false; }
    }

    private static bool FaceModelReady(string url)
    {
        try { return HttpGet(url + "api/status").IndexOf("\"face_model\":true") >= 0; }
        catch { return true; }
    }

    private static void StartScan(string url, List<string> roots, bool withFaces)
    {
        var sb = new StringBuilder("{\"roots\":[");
        for (int i = 0; i < roots.Count; i++)
        {
            if (i > 0) sb.Append(",");
            sb.Append("\"").Append(roots[i].Replace("\\", "\\\\").Replace("\"", "\\\"")).Append("\"");
        }
        sb.Append("],\"with_faces\":").Append(withFaces ? "true" : "false");
        sb.Append(",\"include_system\":false,\"workers\":1}");
        var req = (HttpWebRequest)WebRequest.Create(url + "api/scan");
        req.Method = "POST";
        req.ContentType = "application/json; charset=utf-8";
        req.Timeout = 8000;
        byte[] payload = Encoding.UTF8.GetBytes(sb.ToString());
        req.ContentLength = payload.Length;
        using (var stream = req.GetRequestStream()) stream.Write(payload, 0, payload.Length);
        using (req.GetResponse()) { }
    }
}

internal sealed class FirstRunForm : Form
{
    private readonly ListBox list = new ListBox();
    public readonly List<string> Roots = new List<string>();

    public FirstRunForm()
    {
        Text = "拾光相册";
        Width = 560;
        Height = 380;
        StartPosition = FormStartPosition.CenterScreen;
        AllowDrop = true;
        Font = new Font("Microsoft YaHei UI", 10);
        var hint = new Label();
        hint.Dock = DockStyle.Top;
        hint.Height = 88;
        hint.Padding = new Padding(16, 12, 16, 8);
        hint.Text = "第一次使用：把照片文件夹拖到这里，或点“选择文件夹”。原图不会移动或修改。选好后点“添加并打开”。";
        list.Dock = DockStyle.Fill;
        list.IntegralHeight = false;
        var bar = new FlowLayoutPanel();
        bar.Dock = DockStyle.Bottom;
        bar.Height = 48;
        bar.Padding = new Padding(8);
        var choose = new Button { Text = "选择文件夹", AutoSize = true };
        var skip = new Button { Text = "先打开界面", AutoSize = true };
        var ok = new Button { Text = "添加并打开", AutoSize = true };
        choose.Click += delegate { PickFolder(); };
        skip.Click += delegate { DialogResult = DialogResult.Cancel; Close(); };
        ok.Click += delegate { DialogResult = DialogResult.OK; Close(); };
        bar.Controls.Add(choose);
        bar.Controls.Add(skip);
        bar.Controls.Add(ok);
        Controls.Add(list);
        Controls.Add(bar);
        Controls.Add(hint);
        DragEnter += delegate(object sender, DragEventArgs e)
        {
            if (e.Data.GetDataPresent(DataFormats.FileDrop)) e.Effect = DragDropEffects.Copy;
        };
        DragDrop += delegate(object sender, DragEventArgs e)
        {
            var paths = e.Data.GetData(DataFormats.FileDrop) as string[];
            if (paths == null) return;
            foreach (string path in paths) AddPath(path);
        };
    }

    private void PickFolder()
    {
        using (var dialog = new FolderBrowserDialog())
        {
            dialog.Description = "选择一个照片文件夹";
            if (dialog.ShowDialog(this) == DialogResult.OK) AddPath(dialog.SelectedPath);
        }
    }

    private void AddPath(string path)
    {
        if (string.IsNullOrWhiteSpace(path)) return;
        string full = Path.GetFullPath(path);
        if (File.Exists(full)) full = Path.GetDirectoryName(full);
        if (!Directory.Exists(full)) return;
        if (!Roots.Exists(item => string.Equals(item, full, StringComparison.OrdinalIgnoreCase)))
        {
            Roots.Add(full);
            list.Items.Add(full);
        }
    }
}
