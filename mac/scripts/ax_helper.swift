// Accessibility helper for the local Bilibili desktop app.
//
// This binary is intentionally small and dependency-free. It is the only
// component that touches the macOS Accessibility tree; every user-visible
// effect is verified by reading state back after the action.
//
// Build: swiftc -O -o mac/bin/ax_helper mac/scripts/ax_helper.swift
// The host process (or this binary) must be granted Accessibility by the user.

import AppKit
import ApplicationServices
import CoreGraphics
import Foundation
import Vision

let BILI_BUNDLE = "com.bilibili.bilibiliPC"

struct Node {
    var role: String = ""
    var subrole: String = ""
    var title: String = ""
    var desc: String = ""
    var value: String = ""
    var identifier: String = ""
    var domid: String = ""
    var classes: [String] = []
    var url: String = ""
    var enabled: Bool = false
    var actions: [String] = []
    var x: Double = 0
    var y: Double = 0
    var w: Double = 0
    var h: Double = 0
    var children: [Node] = []
}

func attr(_ element: AXUIElement, _ name: String) -> CFTypeRef? {
    var value: CFTypeRef?
    let err = AXUIElementCopyAttributeValue(element, name as CFString, &value)
    if err != .success { return nil }
    return value
}

func strAttr(_ element: AXUIElement, _ name: String) -> String {
    if let v = attr(element, name) {
        if let s = v as? String { return s }
        if let u = v as? URL { return u.absoluteString ?? "" }
        if let u = v as? NSURL { return u.absoluteString ?? "" }
        if let n = v as? NSNumber { return n.stringValue }
    }
    return ""
}

func boolAttr(_ element: AXUIElement, _ name: String) -> Bool {
    if let v = attr(element, name) as? NSNumber { return v.boolValue }
    return false
}

func pointAttr(_ element: AXUIElement, _ name: String) -> (Double, Double) {
    guard let v = attr(element, name), CFGetTypeID(v) == AXValueGetTypeID() else { return (0, 0) }
    var point = CGPoint.zero
    if AXValueGetValue(v as! AXValue, .cgPoint, &point) { return (Double(point.x), Double(point.y)) }
    return (0, 0)
}

func sizeAttr(_ element: AXUIElement, _ name: String) -> (Double, Double) {
    guard let v = attr(element, name), CFGetTypeID(v) == AXValueGetTypeID() else { return (0, 0) }
    var size = CGSize.zero
    if AXValueGetValue(v as! AXValue, .cgSize, &size) { return (Double(size.width), Double(size.height)) }
    return (0, 0)
}

func actions(_ element: AXUIElement) -> [String] {
    var names: CFArray?
    if AXUIElementCopyActionNames(element, &names) == .success, let arr = names as? [String] {
        return arr
    }
    return []
}

func children(_ element: AXUIElement) -> [AXUIElement] {
    if let v = attr(element, kAXChildrenAttribute), let arr = v as? [AXUIElement] {
        return arr
    }
    return []
}

func walk(_ element: AXUIElement, depth: Int, maxDepth: Int, budget: inout Int) -> Node {
    budget -= 1
    var n = Node()
    n.role = strAttr(element, kAXRoleAttribute)
    n.subrole = strAttr(element, kAXSubroleAttribute)
    n.title = strAttr(element, kAXTitleAttribute)
    n.desc = strAttr(element, kAXDescriptionAttribute)
    n.value = strAttr(element, kAXValueAttribute)
    n.identifier = strAttr(element, kAXIdentifierAttribute)
    n.domid = strAttr(element, "AXDOMIdentifier")
    n.classes = (attr(element, "AXDOMClassList") as? [String]) ?? []
    n.url = strAttr(element, "AXURL")
    n.enabled = boolAttr(element, kAXEnabledAttribute)
    n.actions = actions(element)
    (n.x, n.y) = pointAttr(element, kAXPositionAttribute)
    (n.w, n.h) = sizeAttr(element, kAXSizeAttribute)
    if depth < maxDepth && budget > 0 {
        for child in children(element) {
            if budget <= 0 { break }
            n.children.append(walk(child, depth: depth + 1, maxDepth: maxDepth, budget: &budget))
        }
    }
    return n
}

func jsonObject(_ node: Node) -> [String: Any] {
    return [
        "role": node.role, "subrole": node.subrole, "title": node.title,
        "desc": node.desc, "value": node.value, "identifier": node.identifier,
        "url": node.url, "enabled": node.enabled, "actions": node.actions,
        "domid": node.domid, "classes": node.classes,
        "x": node.x, "y": node.y, "w": node.w, "h": node.h,
        "children": node.children.map(jsonObject),
    ]
}

func emit(_ object: [String: Any]) {
    if let data = try? JSONSerialization.data(withJSONObject: object, options: [.sortedKeys]),
       let text = String(data: data, encoding: .utf8) {
        print(text)
        fflush(stdout)
    }
}

func findApp() -> NSRunningApplication? {
    NSRunningApplication.runningApplications(withBundleIdentifier: BILI_BUNDLE).first
}

func focusedRoot(_ axApp: AXUIElement) -> AXUIElement {
    if let fw = attr(axApp, kAXFocusedWindowAttribute) {
        return fw as! AXUIElement
    }
    if let windows = attr(axApp, kAXWindowsAttribute) as? [AXUIElement], let first = windows.first {
        return first
    }
    return axApp
}

func launchApp() -> Bool {
    if findApp() != nil { return true }
    guard let url = NSWorkspace.shared.urlForApplication(withBundleIdentifier: BILI_BUNDLE) else {
        return false
    }
    let cfg = NSWorkspace.OpenConfiguration()
    cfg.activates = true
    var launched = false
    let sem = DispatchSemaphore(value: 0)
    NSWorkspace.shared.openApplication(at: url, configuration: cfg) { app, error in
        launched = (app != nil && error == nil)
        sem.signal()
    }
    _ = sem.wait(timeout: .now() + 15)
    return launched
}

func descendantURLs(_ element: AXUIElement, depth: Int = 0) -> [String] {
    if depth > 4 { return [] }
    var urls: [String] = []
    let u = strAttr(element, "AXURL")
    if !u.isEmpty { urls.append(u) }
    for child in children(element) { urls.append(contentsOf: descendantURLs(child, depth: depth + 1)) }
    return urls
}

func findElements(_ root: AXUIElement, predicate: (Node) -> Bool, maxDepth: Int = 10,
                  limit: Int = 400) -> [AXUIElement] {
    var result: [AXUIElement] = []
    var budget = 4000
    func visit(_ element: AXUIElement, _ depth: Int) {
        if result.count >= limit || budget <= 0 { return }
        budget -= 1
        var n = Node()
        n.role = strAttr(element, kAXRoleAttribute)
        n.subrole = strAttr(element, kAXSubroleAttribute)
        n.title = strAttr(element, kAXTitleAttribute)
        n.desc = strAttr(element, kAXDescriptionAttribute)
        n.value = strAttr(element, kAXValueAttribute)
        n.identifier = strAttr(element, kAXIdentifierAttribute)
        n.url = strAttr(element, "AXURL")
        if predicate(n) { result.append(element) }
        if depth < maxDepth {
            for child in children(element) { visit(child, depth + 1) }
        }
    }
    visit(root, 0)
    return result
}

func pageURL(_ root: AXUIElement) -> String {
    for e in findElements(root, predicate: { $0.role == "AXWebArea" }, maxDepth: 8, limit: 4) {
        let u = strAttr(e, "AXURL")
        if !u.isEmpty { return u }
    }
    return ""
}

func searchTextField(_ root: AXUIElement) -> AXUIElement? {
    let candidates = findElements(root, predicate: { n in
        ["AXTextField", "AXTextArea", "AXSearchField"].contains(n.role)
    }, maxDepth: 14, limit: 50)
    if candidates.isEmpty { return nil }
    for e in candidates {
        let hay = (strAttr(e, kAXTitleAttribute) + strAttr(e, kAXDescriptionAttribute)
                   + strAttr(e, kAXPlaceholderValueAttribute) + strAttr(e, kAXIdentifierAttribute)).lowercased()
        if hay.contains("搜索") || hay.contains("search") || hay.contains("搜") { return e }
    }
    // The player page exposes the comment box as its only text field. Never
    // type there: report "no search box" so the adapter returns home first.
    let url = pageURL(root)
    if url.contains("player.html") || url.contains("/video/") { return nil }
    // On the home/search page the search box is the visible header field
    // (the other candidate is a 1x0 hidden input).
    for e in candidates {
        let box = sizeAttr(e, kAXSizeAttribute)
        if box.0 > 40 && box.1 > 8 { return e }
    }
    return nil
}

func focusedTextField(_ root: AXUIElement) -> AXUIElement? {
    // set-text intentionally targets whatever single field the caller focused.
    findElements(root, predicate: { n in
        ["AXTextField", "AXTextArea", "AXSearchField"].contains(n.role)
    }, maxDepth: 14, limit: 50).first
}

func setText(_ element: AXUIElement, _ text: String) -> Bool {
    let err = AXUIElementSetAttributeValue(element, kAXValueAttribute as CFString, text as CFTypeRef)
    return err == .success
}

func press(_ element: AXUIElement) -> Bool {
    for action in [kAXPressAction, kAXConfirmAction] {
        if AXUIElementPerformAction(element, action as CFString) == .success { return true }
    }
    return false
}

func activate(_ app: NSRunningApplication) {
    app.activate(options: [.activateIgnoringOtherApps, .activateAllWindows])
}

func sendKey(_ code: CGKeyCode, flags: CGEventFlags = []) {
    let source = CGEventSource(stateID: .hidSystemState)
    let down = CGEvent(keyboardEventSource: source, virtualKey: code, keyDown: true)
    let up = CGEvent(keyboardEventSource: source, virtualKey: code, keyDown: false)
    down?.flags = flags
    up?.flags = flags
    down?.post(tap: .cghidEventTap)
    usleep(30_000)
    up?.post(tap: .cghidEventTap)
}

func typeText(_ text: String) {
    let source = CGEventSource(stateID: .hidSystemState)
    for ch in text {
        var utf16 = Array(String(ch).utf16)
        let down = CGEvent(keyboardEventSource: source, virtualKey: 0, keyDown: true)
        down?.keyboardSetUnicodeString(stringLength: utf16.count, unicodeString: &utf16)
        down?.post(tap: .cghidEventTap)
        usleep(15_000)
        let up = CGEvent(keyboardEventSource: source, virtualKey: 0, keyDown: false)
        up?.keyboardSetUnicodeString(stringLength: utf16.count, unicodeString: &utf16)
        up?.post(tap: .cghidEventTap)
        usleep(22_000)
    }
}

// Vision OCR for CEF-rendered text that never reaches the Accessibility tree
// (favourite folder names, danmaku input, volume label, ...).
func ocrText(x: Double, y: Double, w: Double, h: Double) -> [[String: Any]] {
    guard w > 0, h > 0 else { return [] }
    let path = NSTemporaryDirectory() + "ax_ocr_\(getpid()).png"
    let proc = Process()
    proc.executableURL = URL(fileURLWithPath: "/usr/sbin/screencapture")
    proc.arguments = ["-x", "-R", "\(Int(x)),\(Int(y)),\(Int(w)),\(Int(h))", path]
    try? proc.run()
    proc.waitUntilExit()
    defer { try? FileManager.default.removeItem(atPath: path) }
    guard let image = NSImage(contentsOfFile: path),
          let tiff = image.tiffRepresentation,
          let rep = NSBitmapImageRep(data: tiff),
          let cg = rep.cgImage else { return [] }
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.recognitionLanguages = ["zh-Hans", "en-US"]
    request.usesLanguageCorrection = true
    let handler = VNImageRequestHandler(cgImage: cg, options: [:])
    try? handler.perform([request])
    var rows: [[String: Any]] = []
    for observation in request.results ?? [] {
        guard let candidate = observation.topCandidates(1).first else { continue }
        let bb = observation.boundingBox
        rows.append([
            "text": candidate.string,
            "x": x + Double(bb.minX) * w,
            "y": y + (1 - Double(bb.maxY)) * h,
            "w": Double(bb.width) * w,
            "h": Double(bb.height) * h,
            "confidence": candidate.confidence,
        ])
    }
    return rows
}

func findPlayerButton(_ root: AXUIElement, wantPause: Bool) -> AXUIElement? {
    let candidates = findElements(root, predicate: { n in
        let hay = (n.title + n.desc + n.value).lowercased()
        if n.role == "AXButton" || n.role == "AXMenuItem" {
            if wantPause { return hay.contains("暂停") || hay.contains("pause") }
            return hay.contains("播放") || hay.contains("play")
        }
        return false
    }, maxDepth: 16, limit: 40)
    return candidates.first
}

func findPlaybackTime(_ root: AXUIElement) -> String {
    let nodes = findElements(root, predicate: { n in
        let v = (n.value + n.title).trimmingCharacters(in: .whitespaces)
        return v.range(of: "^[0-9]{1,2}:[0-9]{2}(:[0-9]{2})?$", options: .regularExpression) != nil
    }, maxDepth: 16, limit: 20)
    return nodes.first.map { strAttr($0, kAXValueAttribute) } ?? ""
}

func postPlayPauseKey() {
    let keyType: Int32 = 16 // NX_KEYTYPE_PLAY
    for down in [true, false] {
        let state: Int32 = down ? 0x0A : 0x0B
        let data1 = (keyType << 16) | (state << 8)
        if let event = NSEvent.otherEvent(
            with: .systemDefined, location: .zero, modifierFlags: [],
            timestamp: 0, windowNumber: 0, context: nil, subtype: 8,
            data1: Int(data1), data2: -1
        ) {
            event.cgEvent?.post(tap: .cghidEventTap)
        }
    }
}

func main() {
    let args = CommandLine.arguments
    guard args.count >= 2 else {
        emit(["ok": false, "error": "usage: ax_helper <probe|launch|search|player|press|set-text|key|activate|trust> ..."])
        exit(2)
    }
    let command = args[1]
    if command == "application-info" {
        let app = NSRunningApplication.runningApplications(withBundleIdentifier: BILI_BUNDLE).first
        let installed = NSWorkspace.shared.urlForApplication(withBundleIdentifier: BILI_BUNDLE)
        emit(["ok":true,"running":app != nil,"installed":installed != nil,
              "pid":app.map { Int($0.processIdentifier) } ?? 0,
              "bundle_path":installed?.path ?? ""])
        return
    }
    if command == "trust" {
        emit(["ok": true, "trusted": AXIsProcessTrusted(), "bundle": BILI_BUNDLE])
        return
    }
    // Window metadata does not require Accessibility. Keep this outside the
    // AX trust gate and do not activate/reorder the user's windows to inspect it.
    if command == "cgwindows" {
        guard let app=findApp() else {emit(["ok":false,"windows":[]]);return}
        let list = CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID) as? [[String: Any]] ?? []
        var rows: [[String: Any]] = []
        for w in list {
            let pid = (w[kCGWindowOwnerPID as String] as? NSNumber)?.int32Value ?? 0
            if pid != app.processIdentifier { continue }
            let num = (w[kCGWindowNumber as String] as? NSNumber)?.intValue ?? 0
            let name = w[kCGWindowName as String] as? String ?? ""
            let bounds = w[kCGWindowBounds as String] as? [String: Any] ?? [:]
            rows.append(["id": num, "name": name, "bounds": bounds,
                         "layer": (w[kCGWindowLayer as String] as? NSNumber)?.intValue ?? 0,
                         "alpha": (w[kCGWindowAlpha as String] as? NSNumber)?.doubleValue ?? 0])
        }
        emit(["ok": true, "pid": app.processIdentifier, "windows": rows])
        return
    }
    if command == "launch" {
        let launched = launchApp()
        if let app = findApp() { activate(app) }
        emit(["ok": launched, "running": findApp() != nil, "bundle": BILI_BUNDLE])
        return
    }
    if !AXIsProcessTrusted() {
        emit(["ok": false, "error": "accessibility_not_granted", "trusted": false])
        exit(3)
    }
    guard let app = findApp() else {
        emit(["ok": false, "error": "app_not_running", "running": false])
        exit(4)
    }
    activate(app)
    let axApp = AXUIElementCreateApplication(app.processIdentifier)
    // A busy CEF renderer can block AX replies for tens of seconds. Fail fast
    // instead so a voice command never hangs on a stalled accessibility call.
    AXUIElementSetMessagingTimeout(axApp, 2.0)
    // Chromium enables its accessibility tree when an assistive client marks
    // the application. The set may return -25208 while still arming it, so
    // treat it as a hint and then give the renderer time to publish the tree.
    _ = AXUIElementSetAttributeValue(axApp, "AXEnhancedUserInterface" as CFString, kCFBooleanTrue)
    _ = AXUIElementSetAttributeValue(axApp, "AXManualAccessibility" as CFString, kCFBooleanTrue)
    _ = attr(axApp, kAXFocusedUIElementAttribute)
    usleep(700_000)
    let root = focusedRoot(axApp)
    switch command {
    case "probe":
        var budget = 1200
        let node = walk(root, depth: 0, maxDepth: 10, budget: &budget)
        emit(["ok": true, "trusted": true, "pid": app.processIdentifier,
              "tree": jsonObject(node)])
    case "search":
        guard args.count >= 3 else { emit(["ok": false, "error": "missing query"]); exit(2) }
        guard let field = searchTextField(root) else {
            emit(["ok": false, "error": "search_field_not_found"])
            exit(5)
        }
        _ = setText(field, args[2])
        _ = press(field)
        usleep(250_000)
        sendKey(36) // Return
        // Event-practical wait: Bili's search renderer usually publishes cards
        // within ~1 s. Poll twice instead of always sleeping 2.8 s.
        func findSearchLinks() -> [AXUIElement] {
            findElements(root, predicate: { n in
                let hay = n.title + n.desc + n.value + n.url
                return hay.contains("BV") || n.role == "AXLink" || n.role == "AXButton"
            }, maxDepth: 18, limit: 80)
        }
        usleep(1_200_000)
        var links = findSearchLinks()
        let hasCards = links.contains { n in
            strAttr(n, kAXRoleAttribute) == "AXLink"
                && sizeAttr(n, kAXSizeAttribute).0 >= 250
                && sizeAttr(n, kAXSizeAttribute).1 >= 150
        }
        if !hasCards {
            usleep(700_000)
            links = findSearchLinks()
        }
        let results: [[String: Any]] = links.map { e in
            let (x, y) = pointAttr(e, kAXPositionAttribute)
            let (w, h) = sizeAttr(e, kAXSizeAttribute)
            return ["role": strAttr(e, kAXRoleAttribute), "title": strAttr(e, kAXTitleAttribute),
                    "desc": strAttr(e, kAXDescriptionAttribute), "value": strAttr(e, kAXValueAttribute),
                    "url": strAttr(e, "AXURL"), "child_urls": descendantURLs(e),
                    "x": x, "y": y, "w": w, "h": h]
        }
        emit(["ok": true, "results": results, "search_text": args[2],
              "field_value": strAttr(field, kAXValueAttribute),
              "window_title": strAttr(root, kAXTitleAttribute)])
    case "move-to":
        var mx = 0.0, my = 0.0
        var z = 2
        while z + 1 < args.count {
            switch args[z] {
            case "--x": mx = Double(args[z + 1]) ?? 0; z += 2
            case "--y": my = Double(args[z + 1]) ?? 0; z += 2
            default: z += 1
            }
        }
        let src = CGEventSource(stateID: .hidSystemState)
        CGEvent(mouseEventSource: src, mouseType: .mouseMoved,
                mouseCursorPosition: CGPoint(x: mx, y: my), mouseButton: .left)?.post(tap: .cghidEventTap)
        emit(["ok": true, "x": mx, "y": my])
    case "ensure-window":
        activate(app)
        usleep(250_000)
        let wins = (attr(axApp, kAXWindowsAttribute) as? [AXUIElement]) ?? []
        var target: AXUIElement = focusedRoot(axApp)
        if let main = wins.first(where: { strAttr($0, kAXTitleAttribute).contains("哔哩哔哩") }) {
            target = main
        }
        _ = AXUIElementPerformAction(target, kAXRaiseAction as CFString)
        var pos = CGPoint(x: 0, y: 33)
        var size = CGSize(width: 1400, height: 880)
        let pv = AXValueCreate(.cgPoint, &pos)
        let sv = AXValueCreate(.cgSize, &size)
        _ = AXUIElementSetAttributeValue(target, kAXPositionAttribute as CFString, pv!)
        _ = AXUIElementSetAttributeValue(target, kAXSizeAttribute as CFString, sv!)
        usleep(500_000)
        let (rx, ry) = pointAttr(target, kAXPositionAttribute)
        let (rw, rh) = sizeAttr(target, kAXSizeAttribute)
        emit(["ok": true, "title": strAttr(target, kAXTitleAttribute),
              "x": rx, "y": ry, "w": rw, "h": rh])
    case "set-window":
        var sx = 0.0, sy = 33.0, sw = 1470.0, sh = 900.0
        var nameFilter = ""
        var q = 2
        while q + 1 < args.count {
            switch args[q] {
            case "--x": sx = Double(args[q + 1]) ?? sx; q += 2
            case "--y": sy = Double(args[q + 1]) ?? sy; q += 2
            case "--w": sw = Double(args[q + 1]) ?? sw; q += 2
            case "--h": sh = Double(args[q + 1]) ?? sh; q += 2
            case "--name": nameFilter = args[q + 1]; q += 2
            default: q += 1
            }
        }
        var target = root
        if !nameFilter.isEmpty {
            let wins = (attr(axApp, kAXWindowsAttribute) as? [AXUIElement]) ?? []
            if let match = wins.first(where: { strAttr($0, kAXTitleAttribute).contains(nameFilter) }) {
                target = match
            }
        }
        var pos = CGPoint(x: sx, y: sy)
        var size = CGSize(width: sw, height: sh)
        let pv = AXValueCreate(.cgPoint, &pos)
        let sv = AXValueCreate(.cgSize, &size)
        _ = AXUIElementPerformAction(target, kAXRaiseAction as CFString)
        let e1 = AXUIElementSetAttributeValue(target, kAXPositionAttribute as CFString, pv!)
        let e2 = AXUIElementSetAttributeValue(target, kAXSizeAttribute as CFString, sv!)
        usleep(300_000)
        emit(["ok": e1 == .success && e2 == .success, "position": e1.rawValue, "size": e2.rawValue,
              "x": sx, "y": sy, "w": sw, "h": sh])
    case "webarea":
        let webs = findElements(root, predicate: { n in n.role == "AXWebArea" }, maxDepth: 14, limit: 20)
        var best: AXUIElement? = nil
        var bestURL = ""
        var all: [[String: Any]] = []
        for w in webs {
            let u = strAttr(w, "AXURL")
            all.append(["url": u, "title": strAttr(w, kAXTitleAttribute)])
            if u.contains("bilipc.bilibili.com") && (best == nil || u.count > bestURL.count) {
                best = w
                bestURL = u
            }
        }
        if let w = best {
            emit(["ok": true, "url": bestURL, "title": strAttr(w, kAXTitleAttribute),
                  "loaded": boolAttr(w, kAXLoadedAttribute), "all": all])
        } else {
            emit(["ok": false, "error": "web_area_not_found", "all": all])
        }
    case "window":
        let (wx, wy) = pointAttr(root, kAXPositionAttribute)
        let (ww, wh) = sizeAttr(root, kAXSizeAttribute)
        emit(["ok": true, "title": strAttr(root, kAXTitleAttribute),
              "role": strAttr(root, kAXRoleAttribute),
              "x": wx, "y": wy, "w": ww, "h": wh])
    case "player":
        let pauseButton = findPlayerButton(root, wantPause: true)
        let playButton = findPlayerButton(root, wantPause: false)
        var frame: [String: Double] = [:]
        let playerNodes = findElements(root, predicate: { n in n.desc == "哔哩哔哩播放器" }, maxDepth: 16, limit: 5)
        if let p = playerNodes.first {
            let (x, y) = pointAttr(p, kAXPositionAttribute)
            let (w, h) = sizeAttr(p, kAXSizeAttribute)
            frame = ["x": x, "y": y, "w": w, "h": h]
        }
        emit([
            "ok": true,
            "pause_button": pauseButton != nil,
            "play_button": playButton != nil,
            "time": findPlaybackTime(root),
            "frame": frame,
        ])
    case "media-play-pause":
        // Same HID-consumer path as the second screen's 0xCD media key.
        // This controls the current Now Playing owner; callers must gate it.
        postPlayPauseKey()
        emit(["ok": true, "media_key": "play_pause"])
    case "serve":
        // Persistent player-control daemon. Cache the player/button so normal
        // pause/resume does not re-walk the huge Chromium AX tree.
        var cachedWindow: AXUIElement? = nil
        var cachedPlayer: AXUIElement? = nil

        func discoverPlayer() -> Bool {
            let wins = (attr(axApp, kAXWindowsAttribute) as? [AXUIElement]) ?? []
            for w in wins {
                let players = findElements(w, predicate: { n in n.desc == "哔哩哔哩播放器" }, maxDepth: 18, limit: 8)
                if let p = players.first {
                    cachedWindow = w
                    cachedPlayer = p
                    return true
                }
            }
            cachedWindow = nil; cachedPlayer = nil
            return false
        }

        func playerValid() -> Bool {
            guard let p = cachedPlayer else { return false }
            return strAttr(p, kAXDescriptionAttribute) == "哔哩哔哩播放器"
        }

        func buttonForPlayer() -> AXUIElement? {
            guard let p = cachedPlayer else { return nil }
            return findPlayerButton(p, wantPause: true) ?? findPlayerButton(p, wantPause: false)
        }

        if !discoverPlayer() { emit(["ok": false, "error": "player_not_found"]); return }
        emit(["ok": true, "ready": true])
        while let raw = readLine() {
            let cmd = raw.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
            if cmd == "quit" { break }
            if !playerValid() || buttonForPlayer() == nil {
                _ = discoverPlayer()
            }
            guard let p = cachedPlayer, let b = buttonForPlayer() else {
                emit(["ok": false, "error": "player_not_found"])
                continue
            }
            if cmd == "refresh" {
                _ = discoverPlayer()
                emit(["ok": true, "refreshed": cachedPlayer != nil])
                continue
            }
            if cmd == "snapshot" || cmd == "state" {
                let before = findPlaybackTime(p)
                // A 1 s display needs >1 s between samples to distinguish
                // paused (equal) from playing (advanced) reliably.
                usleep(cmd == "state" ? 1_100_000 : 450_000)
                let after = findPlaybackTime(p)
                var bvid = ""
                if let w = cachedWindow {
                    for u in descendantURLs(w) {
                        if let r = u.range(of: "BV[0-9A-Za-z]{10}", options: .regularExpression) {
                            bvid = String(u[r]); break
                        }
                    }
                }
                let (px, py) = pointAttr(p, kAXPositionAttribute)
                let (pw, ph) = sizeAttr(p, kAXSizeAttribute)
                emit(["ok": true, "bvid": bvid, "time_before": before, "time_after": after,
                      "frame": ["x": px, "y": py, "w": pw, "h": ph]])
                continue
            }
            if cmd == "toggle" {
                let before = findPlaybackTime(p)
                let pressed = press(b)
                usleep(300_000)
                let after = findPlaybackTime(p)
                emit(["ok": pressed, "pressed": pressed, "time_before": before, "time_after": after])
                continue
            }
            if cmd == "window-titles" {
                let options: CGWindowListOption = [.optionOnScreenOnly, .excludeDesktopElements]
                let info = CGWindowListCopyWindowInfo(options, kCGNullWindowID) as? [[String: Any]] ?? []
                let names = info.compactMap { $0[kCGWindowName as String] as? String }
                emit(["ok": true, "titles": names])
                continue
            }
            if cmd == "window" {
                let root = focusedRoot(axApp)
                emit(["ok": true, "title": strAttr(root, kAXTitleAttribute)])
                continue
            }
            if cmd == "bvid" {
                let root = focusedRoot(axApp)
                var found = ""
                for u in descendantURLs(root) {
                    if let r = u.range(of: "BV[0-9A-Za-z]{10}", options: .regularExpression) {
                        found = String(u[r]); break
                    }
                }
                emit(["ok": true, "bvid": found])
                continue
            }
            if cmd == "active" {
                emit(["ok": true, "active": app.isActive])
                continue
            }
            if cmd == "media" {
                guard app.isActive else {
                    emit(["ok": false, "error": "app_not_frontmost"])
                    continue
                }
                postPlayPauseKey()
                emit(["ok": true, "media_key": "play_pause"])
                continue
            }
            emit(["ok": false, "error": "unknown_command"])
        }
        return
    case "player-snapshot":
        // One traversal for frame + bvid + before/after playback time.
        let wins = (attr(axApp, kAXWindowsAttribute) as? [AXUIElement]) ?? []
        for w in wins {
            let players = findElements(w, predicate: { n in n.desc == "哔哩哔哩播放器" }, maxDepth: 18, limit: 8)
            if let p = players.first {
                let (px, py) = pointAttr(p, kAXPositionAttribute)
                let (pw, ph) = sizeAttr(p, kAXSizeAttribute)
                let before = findPlaybackTime(p)
                usleep(450_000)
                let after = findPlaybackTime(p)
                var bvid = ""
                for u in descendantURLs(w) {
                    if let r = u.range(of: "BV[0-9A-Za-z]{10}", options: .regularExpression) {
                        bvid = String(u[r])
                        break
                    }
                }
                emit(["ok": true, "bvid": bvid,
                      "time_before": before, "time_after": after,
                      "frame": ["x": px, "y": py, "w": pw, "h": ph]])
                return
            }
        }
        emit(["ok": false, "error": "player_not_found"])
    case "player-state":
        // Fast state: inspect play/pause buttons inside the real player node.
        let wins = (attr(axApp, kAXWindowsAttribute) as? [AXUIElement]) ?? []
        for w in wins {
            let players = findElements(w, predicate: { n in n.desc == "哔哩哔哩播放器" }, maxDepth: 18, limit: 8)
            for p in players {
                let pause = findPlayerButton(p, wantPause: true)
                let play = findPlayerButton(p, wantPause: false)
                if pause != nil || play != nil {
                    let (px, py) = pointAttr(p, kAXPositionAttribute)
                    let (pw, ph) = sizeAttr(p, kAXSizeAttribute)
                    func buttonInfo(_ b: AXUIElement?) -> [String: Any] {
                        guard let b else { return ["present": false] }
                        let (bx, by) = pointAttr(b, kAXPositionAttribute)
                        let (bw, bh) = sizeAttr(b, kAXSizeAttribute)
                        return ["present": true, "hidden": boolAttr(b, "AXHidden"),
                                "enabled": boolAttr(b, kAXEnabledAttribute),
                                "x": bx, "y": by, "w": bw, "h": bh,
                                "title": strAttr(b, kAXTitleAttribute),
                                "desc": strAttr(b, kAXDescriptionAttribute)]
                    }
                    let pauseInfo = buttonInfo(pause)
                    let playInfo = buttonInfo(play)
                    let pauseVisible = (pauseInfo["hidden"] as? Bool) == false && (pauseInfo["w"] as? Double ?? 0) > 0
                    let playVisible = (playInfo["hidden"] as? Bool) == false && (playInfo["w"] as? Double ?? 0) > 0
                    let playing = pause != nil && play == nil
                    let paused = play != nil && pause == nil
                    emit(["ok": true, "playing": playing, "paused": paused,
                          "ambiguous": pause != nil && play != nil,
                          "pause_button": pause != nil, "play_button": play != nil,
                          "pause_info": pauseInfo, "play_info": playInfo,
                          "pause_visible": pauseVisible, "play_visible": playVisible,
                          "time": findPlaybackTime(p),
                          "frame": ["x": px, "y": py, "w": pw, "h": ph]])
                    return
                }
            }
        }
        emit(["ok": false, "error": "player_controls_not_found"])
    case "player-toggle":
        // player-toggle --target pause|play
        var target = "toggle"
        var q = 2
        while q + 1 < args.count {
            if args[q] == "--target" { target = args[q + 1]; q += 2 } else { q += 1 }
        }
        let wins = (attr(axApp, kAXWindowsAttribute) as? [AXUIElement]) ?? []
        var chosenWindow: AXUIElement? = nil
        var chosenPlayer: AXUIElement? = nil
        var chosenButton: AXUIElement? = nil
        for w in wins {
            let players = findElements(w, predicate: { n in n.desc == "哔哩哔哩播放器" }, maxDepth: 18, limit: 8)
            for p in players {
                let pause = findPlayerButton(p, wantPause: true)
                let play = findPlayerButton(p, wantPause: false)
                let candidate: AXUIElement?
                if target == "pause" { candidate = pause ?? play }
                else if target == "play" { candidate = play ?? pause }
                else { candidate = pause ?? play }
                if let c = candidate {
                    chosenWindow = w; chosenPlayer = p; chosenButton = c
                    break
                }
            }
            if chosenButton != nil { break }
        }
        if let b = chosenButton {
            activate(app)
            let before = chosenPlayer.map { findPlaybackTime($0) } ?? ""
            let pressed = press(b)
            if let w = chosenWindow { _ = AXUIElementPerformAction(w, kAXRaiseAction as CFString) }
            usleep(450_000)
            let after = chosenPlayer.map { findPlaybackTime($0) } ?? ""
            let (px, py) = chosenPlayer.map { pointAttr($0, kAXPositionAttribute) } ?? (0, 0)
            let (pw, ph) = chosenPlayer.map { sizeAttr($0, kAXSizeAttribute) } ?? (0, 0)
            emit(["ok": pressed, "pressed": pressed, "target": target,
                  "time_before": before, "time_after": after,
                  "frame": ["x": px, "y": py, "w": pw, "h": ph]])
            return
        }
        // Last resort: try AXPress on the player node itself.
        for w in wins {
            let players = findElements(w, predicate: { n in n.desc == "哔哩哔哩播放器" }, maxDepth: 18, limit: 8)
            if let p = players.first, press(p) {
                activate(app)
                _ = AXUIElementPerformAction(w, kAXRaiseAction as CFString)
                emit(["ok": true, "pressed": true, "target": target, "player_ax_press": true])
                return
            }
        }
        emit(["ok": false, "pressed": false, "target": target, "error": "player_button_not_found"])
    case "click-at":
        // click-at --x <screen-x> --y <screen-y>
        var cx = 0.0
        var cy = 0.0
        var k = 2
        while k + 1 < args.count {
            switch args[k] {
            case "--x": cx = Double(args[k + 1]) ?? 0; k += 2
            case "--y": cy = Double(args[k + 1]) ?? 0; k += 2
            default: k += 1
            }
        }
        if cx == 0 && cy == 0 { emit(["ok": false, "error": "missing coordinates"]); exit(2) }
        let point = CGPoint(x: cx, y: cy)
        let source = CGEventSource(stateID: .hidSystemState)
        CGEvent(mouseEventSource: source, mouseType: .mouseMoved, mouseCursorPosition: point, mouseButton: .left)?.post(tap: .cghidEventTap)
        usleep(80_000)
        CGEvent(mouseEventSource: source, mouseType: .leftMouseDown, mouseCursorPosition: point, mouseButton: .left)?.post(tap: .cghidEventTap)
        usleep(60_000)
        CGEvent(mouseEventSource: source, mouseType: .leftMouseUp, mouseCursorPosition: point, mouseButton: .left)?.post(tap: .cghidEventTap)
        emit(["ok": true, "x": cx, "y": cy])
    case "press-url":
        // press-url --url <bilibili-video-url-or-bv>
        var needle = ""
        var u = 2
        while u + 1 < args.count {
            if args[u] == "--url" { needle = args[u + 1]; u += 2 } else { u += 1 }
        }
        if needle.isEmpty { emit(["ok": false, "error": "missing url"]); exit(2) }
        let links = findElements(root, predicate: { n in
            guard n.role == "AXLink" else { return false }
            if !n.url.isEmpty && (n.url == needle || n.url.contains(needle)) { return true }
            return false
        }, maxDepth: 22, limit: 120)
        if let e = links.first {
            emit(["ok": press(e), "url": needle, "count": links.count])
        } else {
            emit(["ok": false, "error": "url_not_found", "url": needle, "count": 0])
            exit(8)
        }
    case "press-result":
        // press-result --query <text> --index <1-based>
        var query = ""
        var idx = 1
        var j = 2
        while j + 1 < args.count {
            switch args[j] {
            case "--query": query = args[j + 1]; j += 2
            case "--index": idx = Int(args[j + 1]) ?? 1; j += 2
            default: j += 1
            }
        }
        guard !query.isEmpty else { emit(["ok": false, "error": "missing query"]); exit(2) }
        guard let field = searchTextField(root) else {
            emit(["ok": false, "error": "search_field_not_found"]); exit(5)
        }
        _ = setText(field, query)
        _ = press(field)
        usleep(200_000)
        sendKey(36)
        usleep(3_000_000)
        let candidates = findElements(root, predicate: { n in
            n.role == "AXLink" && !n.desc.isEmpty
        }, maxDepth: 20, limit: 120)
        var scored: [(Double, Double, AXUIElement, String)] = []
        for e in candidates {
            let (x, y) = pointAttr(e, kAXPositionAttribute)
            let (w, h) = sizeAttr(e, kAXSizeAttribute)
            let d = strAttr(e, kAXDescriptionAttribute)
            if w < 250 || h < 150 { continue }
            if ["首页", "精选", "动态", "我的"].contains(d) { continue }
            if x < 90 { continue }
            scored.append((y, x, e, d))
        }
        scored.sort { $0.0 == $1.0 ? $0.1 < $1.1 : $0.0 < $1.0 }
        if idx < 1 || idx > scored.count {
            emit(["ok": false, "error": "result_index_out_of_range", "count": scored.count])
            exit(7)
        }
        let chosen = scored[idx - 1]
        let pressed = press(chosen.2)
        emit(["ok": pressed, "index": idx, "count": scored.count,
              "title": String(chosen.3.prefix(120)), "query": query])
    case "press-dom":
        // --id <substring> [--role AXButton] : press a Chromium element by DOM id/class.
        var domNeedle = ""
        var domRole = ""
        var z = 2
        while z + 1 < args.count {
            switch args[z] {
            case "--id": domNeedle = args[z + 1]; z += 2
            case "--role": domRole = args[z + 1]; z += 2
            default: z += 1
            }
        }
        guard !domNeedle.isEmpty else { emit(["ok": false, "error": "missing id"]); exit(2) }
        let needle = domNeedle.lowercased()
        let matches = findElements(root, predicate: { n in
            if !domRole.isEmpty && n.role != domRole { return false }
            let hay = (n.domid + " " + n.classes.joined(separator: " ")).lowercased()
            return !hay.isEmpty && hay.contains(needle)
        }, maxDepth: 12, limit: 10)
        guard let element = matches.first else {
            emit(["ok": false, "error": "dom_element_not_found", "id": domNeedle]); exit(5)
        }
        let ok = press(element)
        let (ex, ey) = pointAttr(element, kAXPositionAttribute)
        let (ew, eh) = sizeAttr(element, kAXSizeAttribute)
        emit(["ok": ok, "pressed": ok, "role": strAttr(element, kAXRoleAttribute),
              "domid": strAttr(element, "AXDOMIdentifier"), "x": ex, "y": ey, "w": ew, "h": eh])
    case "press":
        // press --title <substring> [--role R] [--index N]
        var title = ""
        var role = ""
        var index = 0
        var i = 2
        while i + 1 < args.count {
            switch args[i] {
            case "--title": title = args[i + 1]; i += 2
            case "--role": role = args[i + 1]; i += 2
            case "--index": index = Int(args[i + 1]) ?? 0; i += 2
            default: i += 1
            }
        }
        let found = findElements(root, predicate: { n in
            (title.isEmpty || (n.title + n.desc + n.value).contains(title))
                && (role.isEmpty || n.role == role)
        }, maxDepth: 18, limit: 40)
        if index < found.count && press(found[index]) {
            emit(["ok": true, "pressed": true, "count": found.count])
        } else {
            emit(["ok": false, "error": "element_not_found_or_not_pressable", "count": found.count])
            exit(6)
        }
    case "type":
        guard args.count >= 3 else { emit(["ok": false, "error": "missing text"]); exit(2) }
        typeText(args[2])
        emit(["ok": true, "text": args[2]])
    case "ocr":
        var ox = 0.0, oy = 0.0, ow = 0.0, oh = 0.0
        var q = 2
        while q + 1 < args.count {
            switch args[q] {
            case "--x": ox = Double(args[q + 1]) ?? 0
            case "--y": oy = Double(args[q + 1]) ?? 0
            case "--w": ow = Double(args[q + 1]) ?? 0
            case "--h": oh = Double(args[q + 1]) ?? 0
            default: break
            }
            q += 2
        }
        emit(["ok": true, "rows": ocrText(x: ox, y: oy, w: ow, h: oh)])
    case "set-text":
        guard args.count >= 3 else { emit(["ok": false, "error": "missing text"]); exit(2) }
        guard let field = focusedTextField(root) else {
            emit(["ok": false, "error": "text_field_not_found"]); exit(5)
        }
        emit(["ok": setText(field, args[2]), "value": strAttr(field, kAXValueAttribute)])
    case "keyseq":
        // --key right --count 12 : one process, N key presses (seek arrowing).
        var seqKey = "right"
        var seqCount = 1
        var z = 2
        while z + 1 < args.count {
            switch args[z] {
            case "--key": seqKey = args[z + 1]; z += 2
            case "--count": seqCount = Int(args[z + 1]) ?? 1; z += 2
            default: z += 1
            }
            }
        let codes: [String: CGKeyCode] = ["space": 49, "return": 36, "escape": 53, "left": 123, "right": 124, "q": 12, "w": 13, "e": 14, "d": 2, "f": 3, "m": 46, "n": 45, "p": 35, "leftbracket": 33, "rightbracket": 30]
        guard let seqCode = codes[seqKey.lowercased()] else {
            emit(["ok": false, "error": "unsupported_key"]); exit(2)
        }
        let total = max(1, min(60, seqCount))
        for _ in 0..<total {
            sendKey(seqCode)
            usleep(70_000)
        }
        emit(["ok": true, "key": seqKey, "count": total])
    case "key":
        guard args.count >= 3 else { emit(["ok": false, "error": "missing key"]); exit(2) }
        let key = args[2].lowercased()
        let codes: [String: CGKeyCode] = ["space": 49, "return": 36, "escape": 53, "left": 123, "right": 124, "q": 12, "w": 13, "e": 14, "d": 2, "f": 3, "m": 46, "n": 45, "p": 35, "leftbracket": 33, "rightbracket": 30]
        if let code = codes[key] { sendKey(code); emit(["ok": true, "key": key]) }
        else { emit(["ok": false, "error": "unsupported_key"]); exit(2) }
    case "activate":
        activate(app)
        emit(["ok": true, "activated": app.isActive])
    default:
        emit(["ok": false, "error": "unknown_command"])
        exit(2)
    }
}

main()
