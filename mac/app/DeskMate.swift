import AppKit
import CoreBluetooth
import ApplicationServices
import CoreGraphics
import ServiceManagement
import UserNotifications

/// A timeout or saved setting is not evidence of missing device credentials.
/// Only the current authenticated channel verifies authorization live.
func deviceAuthorizationLabel(_ ble: [String:Any]) -> String {
    if ble["authenticated"] as? Bool == true { return "授权已验证" }
    let code=ble["error_code"] as? String ?? ""
    if ble["phase"] as? String == "retry" {
        if code == "BLE_AUTH_REQUIRED" { return "需要设备授权" }
        if code == "BLE_AUTH_MISMATCH" { return "需要重新授权" }
    }
    return "授权待连接确认"
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    let resources = Bundle.main.resourceURL!
    let root = ProcessInfo.processInfo.environment["VOICE_ASSISTANT_DATA_DIR"].map { URL(fileURLWithPath: $0) }
        ?? FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Application Support/desk-mate")
    var permissionSnapshot: [String:Any] = [:]
    var checkingPermissions = false
    var permissionCheckedAt = Date.distantPast
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
    var notificationProbe: [String] = []
    var notificationTimes: [String: Date] = [:]
    var serviceFaultSignature = ""
    var observedBLEConnection = false
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
        operationText = [message,hint,detail].filter { !$0.isEmpty }.joined(separator:" · ")
        mainWindow?.update()
        if !code.isEmpty { notifyStatus(message,hint,key:"operation-"+code) }
    }

    var python: URL { resources.appendingPathComponent("python/bin/python3") }

    func applicationDidFinishLaunching(_ notification: Notification) {
        if CommandLine.arguments.contains("--ui-preview") { showWindow(); return }
        try? FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        if let button = statusItem.button {
            let image = NSImage(systemSymbolName: "waveform.circle", accessibilityDescription: "Desk-Mate")
            image?.isTemplate = true
            button.image = image
            button.imagePosition = .imageOnly
            button.toolTip = "Desk-Mate · 启动中"
        }
        let menu=NSMenu();let appItem=NSMenuItem();menu.addItem(appItem)
        let appMenu=NSMenu();appMenu.addItem(NSMenuItem(title:"退出 Desk-Mate",action:#selector(NSApplication.terminate(_:)),keyEquivalent:"q"));appItem.submenu=appMenu;NSApp.mainMenu=menu
        rebuildMenu()
        start()
        showWindow()
        refreshPermissions()
        timer = Timer.scheduledTimer(withTimeInterval: 3, repeats: true) { [weak self] _ in self?.refresh() }
    }

    func applicationShouldHandleReopen(_ sender:NSApplication,hasVisibleWindows:Bool)->Bool { showWindow();return true }
    func applicationDidBecomeActive(_ notification:Notification){
        if !CommandLine.arguments.contains("--ui-preview") { refreshPermissions() }
    }
    /// Query from the real, registered Desk-Mate UI process. A command-line
    /// child has a different TCC responsibility context and is not evidence of
    /// this App's grants, even when it executes the same Mach-O file.
    func refreshPermissions(force: Bool = false, accessibilityResult: Bool? = nil, screenResult: Bool? = nil) {
        precondition(Thread.isMainThread)
        guard !checkingPermissions, force || Date().timeIntervalSince(permissionCheckedAt)>1 else{return}
        checkingPermissions=true
        let checkedAt=Date()
        let accessibilityPreflight=AXIsProcessTrusted()
        let screenPreflight=CGPreflightScreenCaptureAccess()
        permissionSnapshot=[
            "source":"main-app",
            "accessibility":accessibilityResult == true || accessibilityPreflight,
            "screen":screenResult == true || screenPreflight,
            "accessibility_preflight":accessibilityPreflight,
            "screen_preflight":screenPreflight,
            "pid":ProcessInfo.processInfo.processIdentifier,
            "bundle_path":Bundle.main.bundlePath,
            "display_name":Bundle.main.object(forInfoDictionaryKey:"CFBundleDisplayName") as? String ?? "Desk-Mate",
            "version":Bundle.main.object(forInfoDictionaryKey:"CFBundleShortVersionString") as? String ?? "development",
            "build":Bundle.main.object(forInfoDictionaryKey:"CFBundleVersion") as? String ?? "development",
            "checked_at":checkedAt.timeIntervalSince1970
        ]
        if let result=accessibilityResult { permissionSnapshot["accessibility_request_result"]=result }
        if let result=screenResult { permissionSnapshot["screen_request_result"]=result }
        permissionCheckedAt=checkedAt;checkingPermissions=false
        // This is diagnostic state only, never a grant cache. The UI displays
        // the exact same in-process observation written here.
        if let data=try? JSONSerialization.data(withJSONObject:permissionSnapshot,options:[.sortedKeys]) {
            try? data.write(to:root.appendingPathComponent("permission-status.json"),options:.atomic)
        }
        mainWindow?.update()
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
        refreshPermissions()
        mainWindow?.update()
        if !busy && !stopping { reportConnectionState() }
    }

    func notifyStatus(_ title: String, _ body: String, key: String) {
        let now = Date()
        if let previous = notificationTimes[key], now.timeIntervalSince(previous) < 300 { return }
        notificationTimes[key] = now
        if CommandLine.arguments.contains("--notification-self-check") { notificationProbe.append(key); return }
        let center = UNUserNotificationCenter.current()
        center.requestAuthorization(options: [.alert]) { granted, _ in
            guard granted else { return } // Status remains available in the window.
            let content = UNMutableNotificationContent()
            content.title = title; content.body = body
            center.add(UNNotificationRequest(identifier: key, content: content, trigger: nil)) { _ in }
        }
    }

    func reportConnectionState() {
        let health = lastState["health"] as? [String: Any] ?? [:]
        let ble = displayedBLE(health)
        if let expected = pendingDevice, let observed = ble["address"] as? String, observed != expected { return }
        let connected = ble["authenticated"] as? Bool == true
        let code = ble["error_code"] as? String ?? ""
        let refreshing = code == "BLE_AUTH_REFRESH"
        if connected {
            if pendingDevice != nil { notifyStatus("设备已连接", "蓝牙语音通道已就绪。", key: "ble-connected") }
            pendingDevice = nil; observedBLEConnection = true; lastFaultSignature = ""
        }
        if !connected && observedBLEConnection && !refreshing {
            observedBLEConnection = false
            notifyStatus("设备连接已断开", "正在等待设备恢复连接，服务会继续运行。", key: "ble-disconnected")
        }
        let signature = code + "|" + (ble["address"] as? String ?? "")
        // A saved/offline peer on launch is an ordinary waiting state.
        if !connected && !code.isEmpty && !refreshing && pendingDevice != nil, signature != lastFaultSignature {
            lastFaultSignature = signature
            notifyStatus(ble["message"] as? String ?? "连接尚未完成", ble["hint"] as? String ?? "请查看连接状态。", key: "ble-attempt-" + code)
            pendingDevice = nil // Subsequent automatic retries stay quiet.
        }
        let error = lastState["error"] as? String ?? ""
        if error != serviceFaultSignature {
            serviceFaultSignature = error
            if !error.isEmpty && supervisor?.isRunning == true { notifyStatus("服务遇到问题", "请在 Desk-Mate 中查看状态与诊断。", key: "service-error") }
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
        menu.addItem(item("重新连接当前设备", #selector(reconnectDevice), enabled: !busy && !stopping && supervisor != nil && (ble["address"] as? String) != nil))
        menu.addItem(item("断开蓝牙输入", #selector(disconnectDevice), enabled: !busy && !stopping && (ble["address"] as? String) != nil))
        menu.addItem(item("添加设备（USB）…", #selector(pairDevice), enabled: !busy && !stopping))
        let advancedDeviceItem=NSMenuItem(title:"设备高级操作",action:nil,keyEquivalent:"")
        let advancedDeviceMenu=NSMenu(title:"设备高级操作")
        advancedDeviceMenu.addItem(item("重新授权（USB）…",#selector(reauthorizeDevice),enabled:!busy && !stopping))
        advancedDeviceItem.submenu=advancedDeviceMenu;menu.addItem(advancedDeviceItem)
        menu.addItem(item("打开 B站并启用本地控制…", #selector(enableCDP), enabled: !busy))
        menu.addItem(.separator())
        let permissionsItem = NSMenuItem(title: "权限与首次使用", action: nil, keyEquivalent: "")
        let permissionsMenu = NSMenu()
        permissionsMenu.addItem(item("辅助功能…", #selector(openAccessibilityPane)))
        permissionsMenu.addItem(item("屏幕录制…", #selector(openScreenPane)))
        permissionsMenu.addItem(item("蓝牙…", #selector(openBluetoothPane)))
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
        if confirm == nil {
            operationText = title + (text.isEmpty ? "" : " · " + text)
            mainWindow?.update()
            notifyStatus(title, text, key: "result-" + title)
            return true
        }
        NSApp.activate(ignoringOtherApps: true)
        let a = NSAlert()
        a.messageText = title; a.informativeText = text
        a.addButton(withTitle: confirm ?? "好")
        if confirm != nil { a.addButton(withTitle: "取消") }
        return a.runModal() == .alertFirstButtonReturn
    }

    func command(_ args: [String], progress: String = "正在处理，请稍候…", completion: @escaping (Int32, String) -> Void) {
        busy = true; operationText = progress
        operationLabel = ["scan":"扫描设备中","pair":args.contains("--force") ? "重新授权中":"添加设备中","select-device":"切换输入中","reconnect":"重新连接中",
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
                self.alert("未找到语音输入设备", "请保持设备唤醒，在设备连接面板选中这台 Mac，并允许 Desk-Mate 使用蓝牙。仅首次添加设备需要 USB 授权，已有授权无需重复添加。"); return
            }
            let alert = NSAlert(); alert.messageText = "选择 Desk-Mate 蓝牙设备"
            alert.informativeText = "选择后只切换蓝牙连接，语音模型和服务会保持运行。新板子首次使用请通过“添加设备”完成 USB 授权。"
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
        observedBLEConnection = false
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

    @objc func reconnectDevice() {
        guard !busy && !stopping else { return }
        observedBLEConnection=false
        command(["reconnect"],progress:"正在重新连接当前设备，保留原有授权，模型保持运行…") { code, output in
            let result=self.response(output)
            guard code == 0, result["ok"] as? Bool == true else { self.showResult(result,fallback:"设备重连未完成");return }
            self.pendingDevice=result["address"] as? String
            self.lastFaultSignature="";self.refresh()
        }
    }

    @objc func pairDevice() { authorizeUSB(force:false) }
    @objc func reauthorizeDevice() { authorizeUSB(force:true) }

    func authorizeUSB(force:Bool) {
        guard !busy && !stopping else { return }
        let ports = ((try? FileManager.default.contentsOfDirectory(atPath: "/dev")) ?? []).filter { $0.hasPrefix("cu.usbmodem") || $0.hasPrefix("cu.usbserial") }.sorted()
        guard !ports.isEmpty else { alert("未找到 USB 设备", "请通过 USB 连接 Desk-Mate 后重试。"); return }
        let a = NSAlert(); a.messageText = force ? "重新授权当前电脑（USB）":"通过 USB 添加设备"
        a.informativeText = force
            ? "将为所选 USB 设备更换这台电脑的配对凭据，语音连接会重新认证。其他电脑的授权、Wi-Fi 配置及蓝牙配对会保留。仅在设备授权丢失或凭据不匹配时使用，请保持设备唤醒。"
            : "首次添加这台电脑时写入设备授权；若已有本机授权记录，将保留凭据并提示重新连接。其他电脑和 Wi-Fi 配置会保留。已授权设备日常使用无需重复 USB 添加。"
        let list = NSPopUpButton(frame: NSRect(x: 0,y: 0,width: 350,height: 26)); list.addItems(withTitles: ports)
        a.accessoryView = list; a.addButton(withTitle: force ? "更换凭据并授权":"添加设备"); a.addButton(withTitle: "取消")
        NSApp.activate(ignoringOtherApps: true)
        if a.runModal() == .alertFirstButtonReturn {
            var args=["pair","--serial-port","/dev/"+ports[list.indexOfSelectedItem]]
            if force { args.append("--force") }
            command(args,progress:force ? "正在更新本电脑的设备授权，请保持连接…":"正在添加设备并检查本机授权记录…") { code, output in
                self.showResult(self.response(output),fallback:"设备添加未完成")
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

    @objc func resetPermissions() {
        guard alert("重置当前应用的权限记录？","这会清除辅助功能和屏幕录制授权；普通重检不会执行此操作。",confirm:"重置权限") else{return}
        // Ad-hoc signed builds change their cdhash on every rebuild, so a grant
        // from an earlier build no longer applies. Clear the stale TCC record
        // for this bundle id and let macOS prompt for the running copy.
        for service in ["Accessibility", "ScreenCapture"] {
            let p = Process()
            p.executableURL = URL(fileURLWithPath: "/usr/bin/tccutil")
            p.arguments = ["reset", service, "com.desk-mate.server"]
            try? p.run()
            p.waitUntilExit()
        }
        let options = [kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true] as CFDictionary
        _ = AXIsProcessTrustedWithOptions(options)
        mainWindow?.update()
    }

    @objc func recheckPermissions() {
        // Restore the native refresh path: when the user explicitly requests a
        // recheck, ask macOS to refresh this App's authorization (not a child
        // process). macOS alone presents/handles any missing consent.
        let options=[kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String:true] as CFDictionary
        let accessibility=AXIsProcessTrustedWithOptions(options)
        let screen=CGRequestScreenCaptureAccess()
        refreshPermissions(force:true,accessibilityResult:accessibility,screenResult:screen)
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
        if alert("权限与首次使用", "辅助功能：\(ax ? "已授权" : "未授权")\n屏幕录制：\(screen ? "已授权" : "未授权")\n\n菜单「权限与首次使用」可直达系统权限。新设备先通过 USB 添加，再选择蓝牙设备并启用 B站控制。已有设备授权无需重复 USB 添加。macOS 权限更改可能需要退出并重新打开 App。", confirm: "打开权限设置") {
            let options = [kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true] as CFDictionary
            _ = AXIsProcessTrustedWithOptions(options)
            _ = CGRequestScreenCaptureAccess()
            openPrivacyPane("Privacy_Accessibility")
        }
    }

    @objc func diagnostics() {
        refreshPermissions(force:true)
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
                     "辅助功能权限：\(permissionSnapshot["accessibility"] as? Bool == true ? "已授权" : "未授权")",
                     "屏幕录制权限：\(permissionSnapshot["screen"] as? Bool == true ? "已授权" : "未授权")",
                     "权限观察来源：主App进程 PID \(ProcessInfo.processInfo.processIdentifier)",
                     "当前App路径：\(Bundle.main.bundlePath)"]
        for value in [lastState["error"],ble["message"],ble["hint"],stt["reason"],model["warmup_error"]] {
            if let value = value as? String, !value.isEmpty { lines.append(value) }
        }
        lines.append("如需排查详细信息，可使用“导出诊断”。")
        showWindow(); mainWindow?.showDiagnostics(lines.joined(separator:"\n"))
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

// Pure state regression checks must exit before NSApp/AppDelegate creation.
// They do not register a privacy client or contact hardware.
if CommandLine.arguments.contains("--connection-self-check") {
    precondition(deviceAuthorizationLabel(["authenticated":true]) == "授权已验证")
    for code in ["BLE_AUTH_TIMEOUT","BLE_DEVICE_UNAVAILABLE","BLE_CONNECT_TIMEOUT","BLE_SELECT_MAC","BLE_AUTH_REFRESH","BLE_TARGET_MISMATCH",""] {
        precondition(deviceAuthorizationLabel(["phase":"retry","authenticated":false,"error_code":code]) == "授权待连接确认")
    }
    for (code,label) in [("BLE_AUTH_REQUIRED","需要设备授权"),("BLE_AUTH_MISMATCH","需要重新授权")] {
        precondition(deviceAuthorizationLabel(["phase":"retry","error_code":code]) == label)
        precondition(deviceAuthorizationLabel(["phase":"authenticating","error_code":code]) == "授权待连接确认")
    }
    print("connection authorization states: live verified, offline unknown, explicit missing/mismatch PASS");exit(0)
}
if CommandLine.arguments.contains("--permission-probe") {
    // Do not register another privacy client or report a CLI child's grants as
    // the desktop App's. Inspect permission-status.json from the running App.
    fputs("Use the running Desk-Mate App's permission-status.json; CLI privacy probing is disabled.\n",stderr)
    exit(64)
}
let app = NSApplication.shared
let delegate = AppDelegate()
if CommandLine.arguments.contains("--self-check") {
    let titles = delegate.makeMenu().items.map { $0.title }
    let data = try! JSONSerialization.data(withJSONObject: ["menu": titles, "resources":delegate.resources.path])
    print(String(data:data,encoding:.utf8)!)
    exit(0)
}
if CommandLine.arguments.contains("--notification-self-check") {
    func state(_ code:String?,_ connected:Bool)->[String:Any]{
        var ble:[String:Any]=["address":"test-peer","authenticated":connected,"phase":connected ? "authenticated":"retry"]
        if let code=code{ble["error_code"]=code;ble["message"]="Test issue"}
        return ["health":["ble":ble]]
    }
    delegate.lastState=state("BLE_DEVICE_UNAVAILABLE",false);delegate.reportConnectionState();precondition(delegate.notificationProbe.isEmpty)
    delegate.lastState=state(nil,true);delegate.reportConnectionState();precondition(delegate.notificationProbe.isEmpty)
    delegate.lastState=state("BLE_AUTH_REFRESH",false);delegate.reportConnectionState();precondition(delegate.notificationProbe.isEmpty)
    delegate.lastState=state(nil,true);delegate.reportConnectionState();precondition(delegate.notificationProbe.isEmpty)
    delegate.lastState=state("BLE_CONNECT_TIMEOUT",false);delegate.reportConnectionState();delegate.reportConnectionState()
    precondition(delegate.notificationProbe == ["ble-disconnected"])
    delegate.pendingDevice="test-peer";delegate.lastState=state("BLE_AUTH_TIMEOUT",false);delegate.reportConnectionState();delegate.reportConnectionState()
    precondition(delegate.notificationProbe == ["ble-disconnected","ble-attempt-BLE_AUTH_TIMEOUT"])
    print("notification policy: quiet startup, one outage notice, explicit-attempt failure, no repeat PASS");exit(0)
}
if let i=CommandLine.arguments.firstIndex(of:"--render-preview"),CommandLine.arguments.count>i+1 {
    let controller=MainWindowController(delegate:delegate);controller.update();controller.renderPreview(CommandLine.arguments[i+1]);exit(0)
}
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()


/// A real settings window instead of a menu-only UI. It reads the same
/// ``lastState`` the status menu uses and rebuilds itself on every refresh.
final class MainWindowController: NSObject, NSWindowDelegate {
    weak var delegate: AppDelegate?
    private let window: NSWindow
    private let stack = NSStackView()
    private let service = NSTextField(wrappingLabelWithString: "")
    private let connection = NSTextField(wrappingLabelWithString: "")
    private let voice = NSTextField(wrappingLabelWithString: "")
    private let permissions = NSTextField(wrappingLabelWithString: "")
    private let activity = NSTextField(wrappingLabelWithString: "")
    private let details = NSStackView()
    private let diagnosticText = NSTextField(wrappingLabelWithString: "")
    private var startButton: NSButton!, stopButton: NSButton!
    private var selectDeviceButton:NSButton!,reconnectButton:NSButton!,addDeviceButton:NSButton!,disconnectButton:NSButton!,reauthorizeButton:NSButton!
    private let ink = NSColor(red:0.95, green:0.94, blue:0.91, alpha:1)
    private let muted = NSColor(red:0.56, green:0.59, blue:0.59, alpha:1)
    private let blue = NSColor(red:0.55, green:0.74, blue:0.90, alpha:1)

    init(delegate: AppDelegate) {
        self.delegate = delegate
        window = NSWindow(contentRect:NSRect(x:0,y:0,width:620,height:700),
                          styleMask:[.titled,.closable,.miniaturizable,.resizable],backing:.buffered,defer:false)
        super.init()
        window.title = "Desk-Mate"; window.isReleasedWhenClosed=false;window.delegate=self
        window.appearance=NSAppearance(named:.darkAqua)
        window.backgroundColor=NSColor(red:0.02,green:0.03,blue:0.04,alpha:1)
        window.minSize=NSSize(width:580,height:580);window.center()
        let scroll=NSScrollView();scroll.hasVerticalScroller=true;scroll.drawsBackground=false
        stack.orientation = .vertical;stack.alignment = .leading;stack.spacing=18
        stack.edgeInsets=NSEdgeInsets(top:26,left:26,bottom:26,right:26)
        stack.translatesAutoresizingMaskIntoConstraints=false;scroll.documentView=stack
        window.contentView=scroll
        NSLayoutConstraint.activate([stack.widthAnchor.constraint(equalTo:scroll.contentView.widthAnchor)])
        stack.addArrangedSubview(label("Desk-Mate",size:28,bold:true))
        stack.addArrangedSubview(label("设备与本地语音服务",size:13,color:muted))
        selectDeviceButton=button("选择设备",#selector(AppDelegate.selectDevice))
        reconnectButton=button("重新连接",#selector(AppDelegate.reconnectDevice))
        addDeviceButton=button("添加设备…",#selector(AppDelegate.pairDevice))
        disconnectButton=button("断开",#selector(AppDelegate.disconnectDevice))
        card("连接", status:connection, actions:[selectDeviceButton,reconnectButton,addDeviceButton,disconnectButton])
        startButton=button("启动服务",#selector(AppDelegate.start));stopButton=button("停止服务",#selector(AppDelegate.stop))
        card("服务与语音", status:service, actions:[startButton,stopButton,button("启用 B站控制",#selector(AppDelegate.enableCDP))],extra:voice)
        card("权限",status:permissions,actions:[button("辅助功能",#selector(AppDelegate.openAccessibilityPane)),button("屏幕录制",#selector(AppDelegate.openScreenPane)),button("蓝牙",#selector(AppDelegate.openBluetoothPane)),button("重新检测",#selector(AppDelegate.recheckPermissions))])
        activity.font = .systemFont(ofSize:12);activity.textColor=muted;stack.addArrangedSubview(activity)
        let toggle=NSButton(title:"诊断与高级设置",target:self,action:#selector(toggleDetails));toggle.bezelStyle = .inline
        stack.addArrangedSubview(toggle)
        details.orientation = .vertical;details.alignment = .leading;details.spacing=12;details.isHidden=true
        details.addArrangedSubview(row([button("诊断状态",#selector(AppDelegate.diagnostics)),button("打开日志",#selector(AppDelegate.logs)),button("导出诊断",#selector(AppDelegate.exportDiagnostics))]))
        reauthorizeButton=button("重新授权（USB）…",#selector(AppDelegate.reauthorizeDevice))
        details.addArrangedSubview(reauthorizeButton)
        details.addArrangedSubview(button("重置 macOS 权限…",#selector(AppDelegate.resetPermissions)))
        details.addArrangedSubview(label("程序："+Bundle.main.bundlePath+"\n版本："+(Bundle.main.object(forInfoDictionaryKey:"CFBundleShortVersionString") as? String ?? "开发版")+"\n签名："+Self.cdhash(),size:11,color:muted))
        details.addArrangedSubview(label("数据："+delegate.root.path,size:11,color:muted))
        details.addArrangedSubview(label("日志可能包含语音转写，分享前请检查。",size:11,color:muted))
        diagnosticText.font = .monospacedSystemFont(ofSize:11,weight:.regular);diagnosticText.textColor=muted
        details.addArrangedSubview(diagnosticText)
        stack.addArrangedSubview(details)
    }
    func showDiagnostics(_ text:String){diagnosticText.stringValue=text;details.isHidden=false}
    @objc private func toggleDetails(){details.isHidden.toggle()}
    func renderPreview(_ path:String){
        guard let view=window.contentView else{return}
        view.layoutSubtreeIfNeeded()
        guard let bitmap=view.bitmapImageRepForCachingDisplay(in:view.bounds) else{return}
        view.cacheDisplay(in:view.bounds,to:bitmap)
        if let data=bitmap.representation(using:.png,properties:[:]){try? data.write(to:URL(fileURLWithPath:path))}
    }
    func show(){NSApp.activate(ignoringOtherApps:true);window.makeKeyAndOrderFront(nil)}
    func windowWillClose(_ notification:Notification){}
    static func cdhash()->String{
        let p=Process();p.executableURL=URL(fileURLWithPath:"/usr/bin/codesign");p.arguments=["-dvvv",Bundle.main.executablePath ?? ""]
        let pipe=Pipe();p.standardError=pipe;try? p.run();p.waitUntilExit()
        let text=String(data:pipe.fileHandleForReading.readDataToEndOfFile(),encoding:.utf8) ?? ""
        return text.split(separator:"\n").first(where:{$0.hasPrefix("CDHash=")}).map{String($0.dropFirst(7))} ?? "未知"
    }
    private func label(_ text:String,size:CGFloat=14,bold:Bool=false,color:NSColor?=nil)->NSTextField{
        let field=NSTextField(wrappingLabelWithString:text);field.font=bold ? .boldSystemFont(ofSize:size):.systemFont(ofSize:size)
        field.textColor=color ?? ink;return field
    }
    private func button(_ title:String,_ action:Selector)->NSButton{
        let b=NSButton(title:title,target:delegate,action:action);b.bezelStyle = .rounded;b.contentTintColor=blue;return b
    }
    private func row(_ views:[NSView])->NSStackView{
        let r=NSStackView(views:views);r.orientation = .horizontal;r.spacing=10;return r
    }
    private func card(_ title:String,status:NSTextField,actions:[NSView],extra:NSTextField?=nil){
        let box=NSStackView();box.orientation = .vertical;box.alignment = .leading;box.spacing=13
        box.edgeInsets=NSEdgeInsets(top:18,left:18,bottom:18,right:18)
        box.wantsLayer=true;box.layer?.backgroundColor=NSColor(red:0.08,green:0.11,blue:0.13,alpha:1).cgColor;box.layer?.cornerRadius=16
        box.addArrangedSubview(label(title,size:16,bold:true))
        status.font = .systemFont(ofSize:13);status.textColor=ink;box.addArrangedSubview(status)
        if let extra=extra{extra.font = .systemFont(ofSize:12);extra.textColor=muted;box.addArrangedSubview(extra)}
        box.addArrangedSubview(row(actions));stack.addArrangedSubview(box)
        box.widthAnchor.constraint(equalTo:stack.widthAnchor,constant:-52).isActive=true
    }
    func update(){
        guard let d=delegate else{return}
        let health=d.lastState["health"] as? [String:Any] ?? [:]
        let ble=d.displayedBLE(health),stt=health["stt"] as? [String:Any] ?? [:],model=health["decision"] as? [String:Any] ?? [:],cdp=health["cdp"] as? [String:Any] ?? [:]
        let connected=ble["authenticated"] as? Bool == true
        let rawName=ble["device_name"] as? String ?? ""
        let name=rawName.isEmpty ? "Desk-Mate":rawName
        let phase=ble["phase"] as? String ?? "disabled"
        connection.stringValue=connected ? name+" · 已连接" : name+" · "+d.bleLabel(phase)
        connection.stringValue += "\n"+deviceAuthorizationLabel(ble)
        if let message=ble["message"] as? String,!message.isEmpty,!connected{connection.stringValue += "\n"+message}
        let phaseLabel=["starting":"服务准备中","ready":"服务就绪","failed":"服务异常","degraded":"服务待配置","stopped":"服务已停止"][d.lastState["phase"] as? String ?? "starting"] ?? "服务准备中"
        service.stringValue=d.supervisor?.isRunning == true ? phaseLabel : "服务已停止"
        voice.stringValue="语音识别：\(stt["available"] as? Bool == true ? "就绪":"准备中")  ·  决策：\(model["model_loaded"] as? Bool == true ? "就绪":"准备中")\nB站控制：\(cdp["cdp_ready"] as? Bool == true ? "已启用":"未启用")"
        func permission(_ key:String)->String{
            guard let value=d.permissionSnapshot[key] as? Bool else{return d.checkingPermissions ? "检测中":"尚未检测"}
            return value ? "已授权":"未授权"
        }
        permissions.stringValue="辅助功能：\(permission("accessibility"))\n屏幕录制：\(permission("screen"))  ·  蓝牙：\(CBManager.authorization == .allowedAlways ? "已授权":"待授权")"

        activity.stringValue=d.busy ? d.operationLabel : d.operationText
        startButton.isEnabled=d.supervisor == nil && !d.stopping;stopButton.isEnabled=d.supervisor != nil && !d.stopping
        let canChange = !d.busy && !d.stopping
        selectDeviceButton.isEnabled=canChange;addDeviceButton.isEnabled=canChange;reauthorizeButton.isEnabled=canChange
        reconnectButton.isEnabled=canChange && d.supervisor != nil && (ble["address"] as? String) != nil
        disconnectButton.isEnabled=canChange && (ble["address"] as? String) != nil
    }
}
