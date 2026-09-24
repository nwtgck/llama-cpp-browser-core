// Included with --pre-js inside each Emscripten module factory. These are
// application callbacks, not entries in Emscripten's INCOMING_MODULE_JS_API.
var sdbCallbacks = {};
Module['setCallbacks'] = function(callbacks) {
    if (!callbacks || typeof callbacks !== 'object') throw new TypeError('Expected callback object');
    var next = {};
    for (var name of ['onProgress', 'onLog']) {
        if (callbacks[name] !== undefined && typeof callbacks[name] !== 'function') {
            throw new TypeError(name + ' must be a function or undefined');
        }
        next[name] = callbacks[name];
    }
    // Update atomically after both handlers have passed validation.
    sdbCallbacks = next;
};
Module['setCallbacks']({ onProgress: Module['onProgress'], onLog: Module['onLog'] });
function sdbNotify(name, args) {
    var handler = sdbCallbacks[name];
    if (!handler) return;
    try { handler.apply(undefined, args); }
    catch (error) {
        // A UI listener must not throw through C++/Asyncify and strand the core.
        try { console.warn('Image core callback failed: ' + name, error); } catch (_) {}
    }
}
Module['sdbDispatchProgress'] = function(step, steps, seconds) {
    sdbNotify('onProgress', [step, steps, seconds]);
};
Module['sdbDispatchLog'] = function(level, text) {
    sdbNotify('onLog', [level, text]);
};
