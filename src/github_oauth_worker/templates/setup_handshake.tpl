<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Continue setup</title></head>
<body><form id="handshake" action="/setup/handshake" method="post"><input type="hidden" name="origin" id="origin"><input type="hidden" name="installation_id" value="{{ installation_id }}"></form><script>
(() => {
  const message = "authorizing:github";
  const receiveHandshake = (event) => {
    if (event.source !== window.opener || event.data !== message) return;
    window.removeEventListener("message", receiveHandshake);
    document.getElementById("origin").value = event.origin;
    document.getElementById("handshake").submit();
  };
  window.addEventListener("message", receiveHandshake);
  // This initial discovery message has no token, state, or user data.
  if (window.opener) window.opener.postMessage(message, "*");
})();
</script></body></html>
