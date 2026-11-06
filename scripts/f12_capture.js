// ============================================================================
// VOD Announcer - F12 network capture snippet
//
// WHAT IT DOES: while this runs in the browser console it logs every
// fetch/XHR the site makes (endpoint + status + first 1500 chars of the
// response) as [VODCAPTURE] lines.
//
// HOW TO USE:
//   1. Open the site, press F12 -> "Console" tab
//   2. Paste this whole file, press Enter
//   3. Do ONE manual action:
//        - on streamrecorder.io: open the channel page and click DOWNLOAD
//          on a recording (log in first if you have an account)
//        - on savedly.net: upload any small file
//   4. Right-click in the console -> "Save as..." (or select all the
//      [VODCAPTURE] lines, copy) and send the output back.
//
// PRIVACY: the log contains page/API data (no passwords). Signed links it
// may contain expire by themselves. Close the tab to stop capturing.
// ============================================================================
(function () {
  if (window.__vodCapture) { console.log("[VODCAPTURE] already running"); return; }
  window.__vodCapture = true;

  var SKIP = /\.(css|png|jpe?g|webp|gif|svg|ico|woff2?|mp4|m3u8|ts)(\?|$)/i;

  function log(type) {
    var args = Array.prototype.slice.call(arguments, 1);
    console.log.apply(console, ["%c[VODCAPTURE][" + type + "]", "color:#0c0;font-weight:bold"].concat(args));
  }

  // --- fetch ---
  var of = window.fetch;
  window.fetch = function () {
    var args = arguments;
    var req = args[0];
    var url = typeof req === "string" ? req : (req && req.url) || "";
    var method = (args[1] && args[1].method) || (req && req.method) || "GET";
    return of.apply(this, args).then(function (resp) {
      if (!SKIP.test(url)) {
        log("fetch", method, resp.status, url);
        try {
          var ct = resp.headers.get("content-type") || "";
          if (/json|text/i.test(ct)) {
            resp.clone().text().then(function (t) { log("body", String(t).slice(0, 1500)); });
          }
        } catch (e) {}
      }
      return resp;
    });
  };

  // --- XHR (the savedly website uploader uses XHR for part PUTs) ---
  var OO = XMLHttpRequest.prototype.open;
  var OS = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function (m, u) {
    this.__capM = m; this.__capU = u;
    return OO.apply(this, arguments);
  };
  XMLHttpRequest.prototype.send = function (body) {
    var self = this;
    this.addEventListener("load", function () {
      if (!SKIP.test(self.__capU || "")) {
        var size = (body && body.size !== undefined) ? body.size : (body && body.length) || 0;
        log("xhr", self.__capM, self.status, self.__capU, "bodyBytes=" + size);
        try { log("body", String(self.responseText).slice(0, 1500)); } catch (e) {}
      }
    });
    return OS.apply(this, arguments);
  };

  console.log("%c[VODCAPTURE] running! Now do ONE manual download (streamrecorder) " +
    "or ONE manual upload (savedly), then copy every [VODCAPTURE] line and send it.",
    "background:#111;color:#0f0;padding:6px;font-size:14px");
})();
