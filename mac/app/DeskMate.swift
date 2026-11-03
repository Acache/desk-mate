import AppKit
import ApplicationServices
import CoreGraphics
import ServiceManagement

final class AppDelegate: NSObject, NSApplicationDelegate {
    let resources = Bundle.main.resourceURL!
    let root = ProcessInfo.processInfo.environment["VOICE_ASSISTANT_DATA_DIR"].map { URL(fileURLWithPath: $0) }
        ?? FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Application Support/desk-mate")
    var statusItem: NSStatusItem!
    var supervisor: Process?
    var timer: Timer?
    var lastState: [String: Any] = [:]
    var mainWindow: MainWindowController?
    var stopping = false
    var terminating = false
    var busy = false
    var operationText = ""
    var operationLabel = "处理中"
    var lastFaultSignature = ""
    var pendingDevice: String?

    func bleLabel(_ phase: String) -> String {
        return ["disabled":"未选择设备", "disconnected":"已断开", "switching":"正在切换设备",
                "resolving":"正在查找设备", "connecting":"正在连接", "services":"正在检查语音服务",
                "status":"正在读取设备状态", "subscribing":"正在建立语音通道", "authenticating":"正在验证授权",
                "authenticated":"已连接", "retry":"连接失败，正在重试", "saved":"已保存，启动服务后连接",
                "waiting_service":"等待服务就绪"][phase] ?? "正在准备连接"
    }

    func displayedBLE(_ health: [String:Any]) -> [String:Any] {
        var ble = health["ble"] as? [String:Any] ?? [:]
        if (ble["address"] as? String) == nil,
           let data = try? Data(contentsOf:root.appendingPathComponent("desktop.json")),
           let saved = try? JSONSerialization.jsonObject(with:data) as? [String:Any],
           let address = saved["ble_address"] as? String {
            ble["address"] = address
            ble["device_name"] = saved["ble_name"] as? String ?? ""
            ble["phase"] = supervisor?.isRunning == true ? "waiting_service" : "saved"
        }
        return ble
    }

    func response(_ text: String) -> [String: Any] {
        for line in text.split(separator: "\n").reversed() {
            if let data = String(line).data(using: .utf8), let value = try? JSONSerialization.jsonObject(with: data) as? [String: Any] { return value }
        }
        return ["ok":false,"code":"INVALID_RESPONSE","message":"操作未能完成","hint":"请查看诊断状态与日志，然后重试"]
    }

    func showResult(_ result: [String: Any], fallback: String) {
        let message = result["message"] as? String ?? fallback
        let hint = result["hint"] as? String ?? ""
        let code = result["ok"] as? Bool == true ? "" : (result["code"] as? String ?? "")
        let detail = result["detail"] as? String ?? ""
        alert(message, [hint, detail, code.isEmpty ? "" : "错误代码：\(code)"].filter { !$0.isEmpty }.joined(separator: "\n"))
    }

    var python: URL { resources.appendingPathComponent("python/bin/python3") }

    func applicationDidFinishLaunching(_ notification: Notification) {
        try? FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        if let button = statusItem.button {
            let image = NSImage(systemSymbolName: "waveform.circle", accessibilityDescription: "Desk-Mate")
            image?.isTemplate = true
            button.image = image
            button.imagePosition = .imageOnly
            button.toolTip = "Desk-Mate · 启动中"
        }
        rebuildMenu()
        start()
        if CommandLine.arguments.contains("--show-window") {
            DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) { [weak self] in self?.showWindow() }
        }
        timer = Timer.scheduledTimer(withTimeInterval: 3, repeats: true) { [weak self] _ in self?.refresh() }
    }

    func environment() -> [String: String] {
        var env = ProcessInfo.processInfo.environment.filter { !$0.key.hasPrefix("PYTHON") && !$0.key.hasPrefix("DYLD_") }
        env["PYTHONPATH"] = resources.appendingPathComponent("mac/src").path
        env["PYTHONNOUSERSITE"] = "1"
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["VOICE_ASSISTANT_DATA_DIR"] = root.path
        return env
    }

    func process(_ command: [String]) -> Process {
        let p = Process()
        p.executableURL = python
        let ollamaPort = ProcessInfo.processInfo.environment["DESK_MATE_OLLAMA_PORT"] ?? "11435"
        p.arguments = ["-m", "voice_assistant.app_runtime", "--resources", resources.path, "--root", root.path,
                       "--ollama-port",ollamaPort] + command
        p.environment = environment()
        p.currentDirectoryURL = root
        p.standardInput = FileHandle.nullDevice
        return p
    }

    @objc func showWindow() {
        if mainWindow == nil { mainWindow = MainWindowController(delegate: self) }
        mainWindow?.update()
        mainWindow?.show()
    }

    @objc func start() {
        guard supervisor == nil, !stopping else { return }
        let p = process(["serve"])
        let log = root.appendingPathComponent("desktop.log")
        if !FileManager.default.fileExists(atPath: log.path) { FileManager.default.createFile(atPath: log.path, contents: nil) }
        let handle = try? FileHandle(forWritingTo: log)
        handle?.seekToEndOfFile()
        p.standardOutput = handle ?? FileHandle.nullDevice
        p.standardError = handle ?? FileHandle.nullDevice
        p.terminationHandler = { [weak self] _ in
            try? handle?.close()
            DispatchQueue.main.async {
                guard let self = self else { return }
                let expectedStop = self.stopping || self.terminating
                self.supervisor = nil
                self.stopping = false
                self.refresh()
                if !expectedStop && p.terminationStatus != 0 {
                    self.alert("服务未能启动", p.terminationStatus == 2 ? "另一个 Desk-Mate 实例正在使用同一数据目录，请返回原实例操作。" : "请查看诊断状态和日志。退出代码：\(p.terminationStatus)")
                }
                if self.terminating { NSApp.reply(toApplicationShouldTerminate: true) }
            }
        }
        do { try p.run(); supervisor = p }
        catch { alert("无法启动服务", error.localizedDescription) }
    }

    @objc func stop() {
        guard let p = supervisor else { return }
        stopping = true
        p.terminate()
        statusItem.button?.toolTip = "Desk-Mate · 停止中"
    }

    @objc func refresh() {
        if let data = try? Data(contentsOf: root.appendingPathComponent("desktop-state.json")),
           let state = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
            lastState = state
        }
        let phase = lastState["phase"] as? String ?? "starting"
        let labels = ["ready":"服务就绪", "starting":"加载中", "stopped":"已停止", "degraded":"待配置", "failed":"异常"]
        let label: String
        if stopping { label = "停止中" }
        else if supervisor == nil { label = "已停止" }
        else if busy { label = operationLabel }
        else if phase == "failed" { label = "异常" }
        else if pendingDevice != nil { label = "连接设备中" }
        else { label = labels[phase] ?? phase }
        statusItem.button?.toolTip = "Desk-Mate · \(label)"
        rebuildMenu()
        mainWindow?.update()
        if !busy && !stopping { reportConnectionState() }
    }

    func reportConnectionState() {
        let health = lastState["health"] as? [String: Any] ?? [:]
        let ble = displayedBLE(health)
        if let expected = pendingDevice, let observed = ble["address"] as? String, observed != expected { return }
        if let expected = pendingDevice, ble["address"] as? String == expected, ble["authenticated"] as? Bool == true {
            pendingDevice = nil
            DispatchQueue.main.async { self.alert("蓝牙语音输入已连接", "已完成设备认证，可以在设备第3屏发起语音指令。") }
        }
        var code = ble["error_code"] as? String ?? ""
        var message = ble["message"] as? String ?? ""
        var hint = ble["hint"] as? String ?? ""
        if let error = lastState["error"] as? String, !error.isEmpty {
            code = "SERVICE_ERROR"; message = "服务遇到问题"; hint = error + "\n请查看诊断状态或打开日志文件夹。"
        }
        let decision = health["decision"] as? [String:Any] ?? [:]
        let stt = health["stt"] as? [String:Any] ?? [:]
        if code.isEmpty, let reason = stt["reason"] as? String {
            code = "STT_UNAVAILABLE"; message = "语音识别模型未能加载"; hint = reason + "\n请检查安装包完整性，查看日志后重试。"
        }
        if code.isEmpty && decision["warmup"] as? String == "failed" {
            code = "DECISION_UNAVAILABLE"; message = "本地决策模型预热失败"; hint = "请查看本地模型服务日志；如服务已恢复，可重新启动服务重试。"
        }
        if let updated = lastState["updated_at"] as? Double, supervisor?.isRunning == true, Date().timeIntervalSince1970 - updated > 15 {
            code = "STATUS_STALE"; message = "服务状态更新已停滞"; hint = "请查看日志；必要时停止并重新启动服务。"
        }
        if code.isEmpty { lastFaultSignature = ""; return }
        let signature = code + "|" + (ble["address"] as? String ?? "") + "|" + message
        if signature != lastFaultSignature {
            lastFaultSignature = signature
            let result: [String:Any] = ["code":code,"message":message,"hint":hint]
            DispatchQueue.main.async { self.showResult(result,fallback:"连接失败") }
        }
    }

    func item(_ title: String, _ action: Selector?, enabled: Bool = true) -> NSMenuItem {
        let value = NSMenuItem(title: title, action: action, keyEquivalent: "")
        value.target = self
        value.isEnabled = enabled
        return value
    }

    func rebuildMenu() {
        statusItem.menu = makeMenu()
    }

    func makeMenu() -> NSMenu {
        let menu = NSMenu()
        menu.autoenablesItems = false
        let version = Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "开发版"
        menu.addItem(item("Desk-Mate \(version)",nil,enabled:false))
        let health = lastState["health"] as? [String: Any] ?? [:]
        let decision = health["decision"] as? [String: Any] ?? [:]
        let stt = health["stt"] as? [String: Any] ?? [:]
        let ble = displayedBLE(health)
        let cdp = health["cdp"] as? [String: Any] ?? [:]
        menu.addItem(item("语音识别：\((stt["available"] as? Bool == true) ? "就绪" : "未就绪")", nil, enabled: false))
        menu.addItem(item("决策模型：\((decision["model_loaded"] as? Bool == true) ? "已加载" : "未加载")", nil, enabled: false))
        menu.addItem(item("蓝牙：\(bleLabel(ble["phase"] as? String ?? "disabled"))", nil, enabled: false))
        if let address = ble["address"] as? String {
            let name = ble["device_name"] as? String ?? ""
            menu.addItem(item("输入设备：\(name.isEmpty ? String(address.suffix(8)) : name)",nil,enabled:false))
        }
        let cdpReady = cdp["cdp_ready"] as? Bool == true
        let playerReady = cdp["player_available"] as? Bool == true
        menu.addItem(item("B站本地控制：\(cdpReady ? (playerReady ? "播放器已连接" : "已启用，请打开视频") : "未启用")", nil, enabled: false))
        if busy { menu.addItem(item(operationText,nil,enabled:false)) }
        else if pendingDevice != nil { menu.addItem(item("正在连接新设备，等待认证结果…",nil,enabled:false)) }
        menu.addItem(.separator())
        let openItem = item("打开主窗口…", #selector(showWindow))
        openItem.keyEquivalent = "o"
        menu.addItem(openItem)
        menu.addItem(.separator())
        menu.addItem(item("启动服务", #selector(start), enabled: supervisor == nil && !stopping))
        menu.addItem(item("停止服务", #selector(stop), enabled: supervisor != nil && !stopping))
        menu.addItem(item("选择或切换蓝牙输入…", #selector(selectDevice), enabled: !busy && !stopping))
        menu.addItem(item("断开蓝牙输入", #selector(disconnectDevice), enabled: !busy && !stopping && (ble["address"] as? String) != nil))
        menu.addItem(item("通过 USB 授权设备…", #selector(pairDevice), enabled: !busy))
        menu.addItem(item("打开 B站并启用本地控制…", #selector(enableCDP), enabled: !busy))
        menu.addItem(.separator())
        let permissionsItem = NSMenuItem(title: "权限与首次使用", action: nil, keyEquivalent: "")
        let permissionsMenu = NSMenu()
        permissionsMenu.addItem(item("辅助功能…", #selector(openAccessibilityPane)))
        permissionsMenu.addItem(item("屏幕录制…", #selector(openScreenPane)))
        permissionsMenu.addItem(item("蓝牙…", #selector(openBluetoothPane)))
        permissionsMenu.addItem(item("麦克风…", #selector(openMicrophonePane)))
        permissionsMenu.addItem(.separator())
        permissionsMenu.addItem(item("权限说明与检查…", #selector(permissions)))
        permissionsItem.submenu = permissionsMenu
        menu.addItem(permissionsItem)
        menu.addItem(item("查看诊断状态…", #selector(diagnostics)))
        menu.addItem(item("打开日志文件夹", #selector(logs)))
        menu.addItem(item("导出诊断…", #selector(exportDiagnostics), enabled: !busy))
        let login = item("登录时启动", #selector(toggleLogin))
        login.state = SMAppService.mainApp.status == .enabled ? .on : .off
        menu.addItem(login)
        menu.addItem(item("退出 Desk-Mate", #selector(quit)))
        return menu
    }

    @discardableResult func alert(_ title: String, _ text: String, confirm: String? = nil) -> Bool {
        NSApp.activate(ignoringOtherApps: true)
        let a = NSAlert()
        a.messageText = title; a.informativeText = text
        a.addButton(withTitle: confirm ?? "好")
        if confirm != nil { a.addButton(withTitle: "取消") }
        return a.runModal() == .alertFirstButtonReturn
    }

    func command(_ args: [String], progress: String = "正在处理，请稍候…", completion: @escaping (Int32, String) -> Void) {
        busy = true; operationText = progress
        operationLabel = ["scan":"扫描设备中","pair":"USB授权中","select-device":"切换输入中",
                          "enable-cdp":"连接B站中","export":"导出诊断中"][args.first ?? ""] ?? "处理中"
        refresh()
        let p = process(args)
        DispatchQueue.global(qos: .userInitiated).async {
            let pipe = Pipe(); p.standardOutput = pipe; p.standardError = pipe
            do {
                try p.run()
                let timeoutLock = NSLock()
                var didTimeOut = false
                let timeout = DispatchWorkItem {
                    if p.isRunning { timeoutLock.lock(); didTimeOut = true; timeoutLock.unlock(); p.terminate() }
                }
                DispatchQueue.global().asyncAfter(deadline: .now() + (args.first == "pair" ? 90 : 30), execute: timeout)
                let data = pipe.fileHandleForReading.readDataToEndOfFile()
                p.waitUntilExit()
                timeout.cancel()
                timeoutLock.lock(); let expired = didTimeOut; timeoutLock.unlock()
                let text = expired ? "{\"ok\":false,\"code\":\"OPERATION_TIMEOUT\",\"message\":\"操作超时\",\"hint\":\"请检查设备或客户端状态，然后重试；详细信息可查看诊断日志\"}" : (String(data: data, encoding: .utf8) ?? "")
                DispatchQueue.main.async { self.busy = false; self.refresh(); completion(p.terminationStatus, text) }
            } catch {
                let result: [String:Any] = ["ok":false,"code":"COMMAND_LAUNCH_FAILED","message":"无法启动操作组件",
                                            "hint":"请检查安装包是否完整并查看诊断日志。","detail":error.localizedDescription]
                let data = try? JSONSerialization.data(withJSONObject:result)
                let text = data.flatMap { String(data:$0,encoding:.utf8) } ?? ""
                DispatchQueue.main.async { self.busy = false; self.refresh(); completion(-1, text) }
            }
        }
    }

    @objc func selectDevice() {
        command(["scan"], progress:"正在扫描蓝牙输入设备…") { code, output in
            let result = self.response(output)
            guard code == 0, result["ok"] as? Bool == true else { self.showResult(result,fallback:"扫描失败"); return }
            guard let devices = result["devices"] as? [[String:String]], !devices.isEmpty else {
                self.alert("未找到语音输入设备", "请保持设备唤醒，在第2屏连接这台 Mac，并允许 Desk-Mate 使用蓝牙。首次使用还需要通过 USB 授权。"); return
            }
            let alert = NSAlert(); alert.messageText = "选择 Desk-Mate 蓝牙设备"
            alert.informativeText = "选择后只切换蓝牙连接，语音模型和服务会保持运行。新板子首次使用需要通过 USB 授权。"
            let list = NSPopUpButton(frame: NSRect(x: 0, y: 0, width: 400, height: 26))
            list.addItems(withTitles: devices.map { "\($0["name"] ?? "设备") · \($0["address"] ?? "")" })
            alert.accessoryView = list; alert.addButton(withTitle: "保存"); alert.addButton(withTitle: "取消")
            NSApp.activate(ignoringOtherApps: true)
            if alert.runModal() == .alertFirstButtonReturn {
                let selected = devices[list.indexOfSelectedItem]
                guard let address = selected["address"], !address.isEmpty else {
                    self.alert("设备地址无效", "请重新扫描并选择设备。"); return
                }
                self.changeDevice(address:address,name:String((selected["name"] ?? "Desk-Mate").prefix(80)))
            }
        }
    }

    func changeDevice(address: String?, name: String = "") {
        var args = ["select-device"]
        if let address = address { args += ["--address",address,"--name",name] }
        command(args,progress:address == nil ? "正在断开蓝牙输入…" : "正在切换蓝牙输入，模型保持运行…") { code, output in
            let result = self.response(output)
            guard code == 0, result["ok"] as? Bool == true else { self.showResult(result,fallback:"设备切换失败"); return }
            if let selected = result["address"] as? String, result["code"] as? String != "BLE_SAVED", result["authenticated"] as? Bool != true {
                self.pendingDevice = selected
                self.lastFaultSignature = ""
                self.refresh()
            } else {
                self.pendingDevice = nil
                self.showResult(result,fallback:"设备设置已更新")
            }
        }
    }

    @objc func disconnectDevice() { changeDevice(address:nil) }

    @objc func pairDevice() {
        let ports = ((try? FileManager.default.contentsOfDirectory(atPath: "/dev")) ?? []).filter { $0.hasPrefix("cu.usbmodem") || $0.hasPrefix("cu.usbserial") }.sorted()
        guard !ports.isEmpty else { alert("未找到 USB 设备", "请通过 USB 连接 Desk-Mate 后重试。"); return }
        let a = NSAlert(); a.messageText = "通过 USB 授权设备"
        a.informativeText = "将写入此安装实例的配对凭据，替换设备上的原服务端授权。保持设备唤醒。"
        let list = NSPopUpButton(frame: NSRect(x: 0,y: 0,width: 350,height: 26)); list.addItems(withTitles: ports)
        a.accessoryView = list; a.addButton(withTitle: "授权"); a.addButton(withTitle: "取消")
        NSApp.activate(ignoringOtherApps: true)
        if a.runModal() == .alertFirstButtonReturn {
            command(["pair","--serial-port","/dev/"+ports[list.indexOfSelectedItem]],progress:"正在通过 USB 授权设备，请保持连接…") { code, output in
                self.showResult(self.response(output),fallback:"设备授权未完成")
            }
        }
    }

    @objc func enableCDP() {
        openBilibili(confirmed:false)
    }

    func openBilibili(confirmed: Bool) {
        command(confirmed ? ["enable-cdp","--confirm-restart"] : ["enable-cdp"],progress:"正在打开 B站并连接本地控制…") { code, output in
            let result = self.response(output)
            if !confirmed && result["requires_restart"] as? Bool == true {
                if self.alert(result["message"] as? String ?? "需要重启 B站", "重启会中断当前播放。确认后将重新打开 B站并启用本地控制。",confirm:"重启并启用") {
                    self.openBilibili(confirmed:true)
                }
            } else {
                self.showResult(result,fallback:"B站本地控制操作未完成")
            }
        }
    }

    @objc func openAccessibilityPane() { openPrivacyPane("Privacy_Accessibility") }
    @objc func openScreenPane() { openPrivacyPane("Privacy_ScreenCapture") }
    @objc func openBluetoothPane() { openPrivacyPane("Privacy_Bluetooth") }
    @objc func openMicrophonePane() { openPrivacyPane("Privacy_Microphone") }

    /// Open the exact privacy sub-pane. macOS 13+ moved these anchors under
    /// PrivacySecurity.extension; older systems use preference.security. Try
    /// the modern URL first and fall back to the legacy one and then the root.
    func openPrivacyPane(_ anchor: String) {
        let candidates = [
            "x-apple.systempreferences:com.apple.settings.PrivacySecurity.extension?\(anchor)",
            "x-apple.systempreferences:com.apple.preference.security?\(anchor)",
            "x-apple.systempreferences:com.apple.preference.security",
        ]
        for raw in candidates {
            if let url = URL(string: raw), NSWorkspace.shared.open(url) { return }
        }
    }

    @objc func permissions() {
        let ax = AXIsProcessTrusted()
        let screen = CGPreflightScreenCaptureAccess()
        if alert("权限与首次使用", "辅助功能：\(ax ? "已授权" : "未授权")\n屏幕录制：\(screen ? "已授权" : "未授权")\n\n菜单「权限与首次使用」可直达这四项权限。随后通过 USB 授权设备，选择蓝牙设备，再启用 B站本地控制。授权后请退出并重新打开 App。", confirm: "打开权限设置") {
            let options = [kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true] as CFDictionary
            _ = AXIsProcessTrustedWithOptions(options)
            _ = CGRequestScreenCaptureAccess()
            openPrivacyPane("Privacy_Accessibility")
        }
    }

    @objc func diagnostics() {
        let health = lastState["health"] as? [String:Any] ?? [:]
        let ble = displayedBLE(health)
        let stt = health["stt"] as? [String:Any] ?? [:]
        let model = health["decision"] as? [String:Any] ?? [:]
        let cdp = health["cdp"] as? [String:Any] ?? [:]
        var lines = ["服务：\(supervisor?.isRunning == true ? "正在运行" : "已停止")",
                     "语音识别：\(stt["available"] as? Bool == true ? "已就绪" : "尚未就绪")",
                     "决策模型：\(model["model_loaded"] as? Bool == true ? "已加载" : "尚未加载")",
                     "蓝牙：\(bleLabel(ble["phase"] as? String ?? "disabled"))",
                     "输入设备：\(ble["device_name"] as? String ?? ble["address"] as? String ?? "未选择")",
                     "B站本地控制：\(cdp["cdp_ready"] as? Bool == true ? "已启用" : "未启用")",
                     "辅助功能权限：\(AXIsProcessTrusted() ? "已授权" : "未授权")",
                     "屏幕录制权限：\(CGPreflightScreenCaptureAccess() ? "已授权" : "未授权")"]
        for value in [lastState["error"],ble["message"],ble["hint"],stt["reason"],model["warmup_error"]] {
            if let value = value as? String, !value.isEmpty { lines.append(value) }
        }
        lines.append("如需排查详细信息，可使用“导出诊断”。")
        alert("诊断状态",lines.joined(separator:"\n"))
    }
    @objc func logs() { NSWorkspace.shared.open(root.appendingPathComponent("logs")) }
    @objc func exportDiagnostics() {
        let panel = NSSavePanel()
        panel.nameFieldStringValue = "Desk-Mate-diagnostics.zip"
        NSApp.activate(ignoringOtherApps: true)
        if panel.runModal() == .OK, let url = panel.url {
            command(["export","--output",url.path]) { code, output in
                self.alert(code == 0 ? "诊断已导出" : "导出失败", code == 0 ? url.path : String(output.suffix(500)))
            }
        }
    }
    @objc func toggleLogin() {
        do {
            if SMAppService.mainApp.status == .enabled { try SMAppService.mainApp.unregister() }
            else {
                try SMAppService.mainApp.register()
                if SMAppService.mainApp.status == .requiresApproval { SMAppService.openSystemSettingsLoginItems() }
            }
            rebuildMenu()
        } catch { alert("无法修改登录启动",error.localizedDescription) }
    }
    @objc func quit() { NSApp.terminate(nil) }
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        if supervisor != nil { terminating = true; stop(); return .terminateLater }
        return .terminateNow
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
if CommandLine.arguments.contains("--self-check") {
    let titles = delegate.makeMenu().items.map { $0.title }
    let data = try! JSONSerialization.data(withJSONObject: ["menu": titles, "resources":delegate.resources.path])
    print(String(data:data,encoding:.utf8)!)
    exit(0)
}
app.delegate = delegate
app.setActivationPolicy(.accessory)
app.run()


/// A real settings window instead of a menu-only UI. It reads the same
/// ``lastState`` the status menu uses and rebuilds itself on every refresh.
final class MainWindowController: NSObject, NSWindowDelegate {
    weak var delegate: AppDelegate?
    private let window: NSWindow
    private let stack = NSStackView()

    init(delegate: AppDelegate) {
        self.delegate = delegate
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 560, height: 600),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable],
                          backing: .buffered, defer: false)
        super.init()
        window.title = "Desk-Mate"
        window.isReleasedWhenClosed = false
        window.delegate = self
        window.center()
        stack.orientation = .vertical
        stack.alignment = .leading
        stack.spacing = 10
        stack.edgeInsets = NSEdgeInsets(top: 18, left: 18, bottom: 18, right: 18)
        stack.translatesAutoresizingMaskIntoConstraints = false
        let host = NSView()
        host.addSubview(stack)
        NSLayoutConstraint.activate([
            stack.topAnchor.constraint(equalTo: host.topAnchor),
            stack.leadingAnchor.constraint(equalTo: host.leadingAnchor),
            stack.trailingAnchor.constraint(lessThanOrEqualTo: host.trailingAnchor),
        ])
        window.contentView = host
    }

    func show() {
        NSApp.activate(ignoringOtherApps: true)
        window.makeKeyAndOrderFront(nil)
    }

    func windowWillClose(_ notification: Notification) {}

    private func label(_ text: String, size: CGFloat = 13, bold: Bool = false, color: NSColor = .labelColor) -> NSTextField {
        let field = NSTextField(labelWithString: text)
        field.font = bold ? NSFont.boldSystemFont(ofSize: size) : NSFont.systemFont(ofSize: size)
        field.textColor = color
        return field
    }

    private func button(_ title: String, _ action: Selector, enabled: Bool = true) -> NSButton {
        let b = NSButton(title: title, target: delegate, action: action)
        b.bezelStyle = .rounded
        b.isEnabled = enabled
        return b
    }

    private func row(_ views: [NSView]) -> NSStackView {
        let r = NSStackView(views: views)
        r.orientation = .horizontal
        r.spacing = 8
        r.alignment = .centerY
        return r
    }

    func update() {
        guard let d = delegate else { return }
        stack.arrangedSubviews.forEach { $0.removeFromSuperview() }
        let health = d.lastState["health"] as? [String: Any] ?? [:]
        let ble = health["ble"] as? [String: Any] ?? [:]
        let stt = health["stt"] as? [String: Any] ?? [:]
        let model = health["decision"] as? [String: Any] ?? [:]
        let cdp = health["cdp"] as? [String: Any] ?? [:]
        let phase = d.lastState["phase"] as? String ?? "starting"

        stack.addArrangedSubview(label("Desk-Mate", size: 20, bold: true))
        stack.addArrangedSubview(label("本地语音控制台 · 服务 \(d.supervisor != nil ? "运行中" : "已停止")（\(phase)）", color: .secondaryLabelColor))

        stack.addArrangedSubview(label("服务", bold: true))
        stack.addArrangedSubview(row([button("启动服务", #selector(AppDelegate.start), enabled: d.supervisor == nil),
                                      button("停止服务", #selector(AppDelegate.stop), enabled: d.supervisor != nil),
                                      button("诊断状态…", #selector(AppDelegate.diagnostics)),
                                      button("打开日志", #selector(AppDelegate.logs))]))

        stack.addArrangedSubview(label("蓝牙语音输入", bold: true))
        let device = (ble["device_name"] as? String) ?? (ble["address"] as? String) ?? "未选择"
        stack.addArrangedSubview(label("阶段：\(d.bleLabel(ble["phase"] as? String ?? "disabled")) · 设备：\(device)"))
        stack.addArrangedSubview(row([button("选择或切换蓝牙输入…", #selector(AppDelegate.selectDevice)),
                                      button("断开蓝牙输入", #selector(AppDelegate.disconnectDevice)),
                                      button("通过 USB 授权设备…", #selector(AppDelegate.pairDevice))]))

        stack.addArrangedSubview(label("B 站本地控制", bold: true))
        let cdpReady = cdp["cdp_ready"] as? Bool == true
        let playerReady = cdp["player_available"] as? Bool == true
        stack.addArrangedSubview(label(cdpReady ? (playerReady ? "本地控制已连接（播放器在线）" : "本地控制已启用，请打开视频") : "未启用本地控制"))
        stack.addArrangedSubview(row([button("打开 B 站并启用本地控制…", #selector(AppDelegate.enableCDP))]))

        stack.addArrangedSubview(label("语音识别 / 决策模型", bold: true))
        stack.addArrangedSubview(label("语音识别：\(stt["available"] as? Bool == true ? "已就绪" : "尚未就绪") · 决策模型：\(model["model_loaded"] as? Bool == true ? "已加载" : "尚未加载")"))

        stack.addArrangedSubview(label("权限", bold: true))
        stack.addArrangedSubview(label("辅助功能：\(AXIsProcessTrusted() ? "已授权" : "未授权") · 屏幕录制：\(CGPreflightScreenCaptureAccess() ? "已授权" : "未授权")"))
        stack.addArrangedSubview(row([button("辅助功能…", #selector(AppDelegate.openAccessibilityPane)),
                                      button("屏幕录制…", #selector(AppDelegate.openScreenPane)),
                                      button("蓝牙…", #selector(AppDelegate.openBluetoothPane)),
                                      button("麦克风…", #selector(AppDelegate.openMicrophonePane))]))

        stack.addArrangedSubview(label("诊断与数据", bold: true))
        stack.addArrangedSubview(row([button("导出诊断…", #selector(AppDelegate.exportDiagnostics)),
                                      button("权限说明…", #selector(AppDelegate.permissions))]))
        if let error = d.lastState["error"] as? String, !error.isEmpty {
            stack.addArrangedSubview(label("最近错误：\(error)", color: .systemRed))
        }
        stack.addArrangedSubview(label("数据目录：\(d.root.path)", size: 11, color: .secondaryLabelColor))
        stack.addArrangedSubview(label("日志中可能包含语音转写，分享前请自行检查。", size: 11, color: .secondaryLabelColor))
    }
}
