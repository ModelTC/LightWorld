"""Start the LightWorld player in the current Blender window."""

import os
import sys

from lightworld_walk import run


def set_player_labels():
    from ctypes import CDLL, CFUNCTYPE, POINTER, byref, c_char_p, c_long, c_void_p

    objc = CDLL("/usr/lib/libobjc.A.dylib")
    get_class = objc.objc_getClass
    selector = objc.sel_registerName
    for lookup in (get_class, selector):
        lookup.argtypes = (c_char_p,)
        lookup.restype = c_void_p

    # Give each Objective-C call its exact arguments on Apple Silicon.
    send = CFUNCTYPE(c_void_p, c_void_p, c_void_p)(("objc_msgSend", objc))
    make_string = CFUNCTYPE(c_void_p, c_void_p, c_void_p, c_char_p)(("objc_msgSend", objc))
    set_object = CFUNCTYPE(None, c_void_p, c_void_p, c_void_p)(("objc_msgSend", objc))
    app = send(get_class(b"NSApplication"), selector(b"sharedApplication"))
    string_class = get_class(b"NSString")
    utf8_string = selector(b"stringWithUTF8String:")
    set_title = selector(b"setTitle:")

    def set_about_version(item):
        send_with_object = CFUNCTYPE(c_void_p, c_void_p, c_void_p, c_void_p)(("objc_msgSend", objc))
        bundle = send(get_class(b"NSBundle"), selector(b"mainBundle"))
        version_key = make_string(string_class, utf8_string, b"CFBundleShortVersionString")
        version = send_with_object(bundle, selector(b"objectForInfoDictionaryKey:"), version_key)
        if not version:
            return
        prefix = make_string(string_class, utf8_string, b"Version ")
        version_text = send_with_object(prefix, selector(b"stringByAppendingString:"), version)
        appkit = CDLL("/System/Library/Frameworks/AppKit.framework/AppKit")
        option_key = c_void_p.in_dll(appkit, "NSAboutPanelOptionApplicationVersion").value
        make_options = CFUNCTYPE(c_void_p, c_void_p, c_void_p, c_void_p, c_void_p)(("objc_msgSend", objc))
        options = make_options(get_class(b"NSDictionary"), selector(b"dictionaryWithObject:forKey:"),
                               version_text, option_key)

        show_about = selector(b"orderFrontStandardAboutPanelWithOptions:")
        signature = send_with_object(app, selector(b"methodSignatureForSelector:"), show_about)
        invocation = send_with_object(get_class(b"NSInvocation"),
                                      selector(b"invocationWithMethodSignature:"), signature)
        set_object(invocation, selector(b"setTarget:"), app)
        set_object(invocation, selector(b"setSelector:"), show_about)
        set_argument = CFUNCTYPE(None, c_void_p, c_void_p, POINTER(c_void_p), c_long)(("objc_msgSend", objc))
        set_argument(invocation, selector(b"setArgument:atIndex:"), byref(c_void_p(options)), 2)
        send_void = CFUNCTYPE(None, c_void_p, c_void_p)(("objc_msgSend", objc))
        send_void(invocation, selector(b"retainArguments"))

        # The menu keeps the native invocation alive across scene reloads.
        set_object(item, selector(b"setRepresentedObject:"), invocation)
        set_object(item, selector(b"setTarget:"), invocation)
        set_object(item, selector(b"setAction:"), selector(b"invoke"))

    window = send(app, selector(b"mainWindow"))
    if not window:
        window = send(send(app, selector(b"orderedWindows")), selector(b"firstObject"))
    if window:
        title = make_string(string_class, utf8_string, b"LightWorld")
        set_object(window, selector(b"setRepresentedURL:"), None)
        set_object(window, set_title, title)

    items = selector(b"itemArray")
    menu = send(app, selector(b"mainMenu"))
    header = send(send(menu, items), selector(b"firstObject"))
    commands = send(send(header, selector(b"submenu")), items)
    enumerator = send(commands, selector(b"objectEnumerator"))
    next_item = selector(b"nextObject")
    action = selector(b"action")
    about = selector(b"orderFrontStandardAboutPanel:")
    labels = {
        about: b"About LightWorld",
        selector(b"hide:"): b"Hide LightWorld",
        selector(b"terminate:"): b"Quit LightWorld",
    }
    while item := send(enumerator, next_item):
        command = send(item, action)
        label = labels.get(command)
        if label:
            set_object(item, set_title, make_string(string_class, utf8_string, label))
        if command == about:
            set_about_version(item)


if sys.platform == "darwin" and os.environ.get("LIGHTWORLD_STATE_DIR"):
    try:
        set_player_labels()
    except (AttributeError, ImportError, OSError, ValueError) as error:
        print(f"[LightWorld] Could not label the player window or menu: {error}", flush=True)

run()
