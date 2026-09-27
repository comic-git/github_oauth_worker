<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Authorizing</title></head>
<body><form id="handshake" action="{{ endpoint }}" method="post"><input type="hidden" name="origin" id="origin"></form><script>
(() => {
  const message = "authorizing:github";
  const receiveHandshake = (event) => {
    if (event.source !== window.opener || event.data !== message) return;
    window.removeEventListener("message", receiveHandshake);
    document.getElementById("origin").value = event.origin;
    document.getElementById("handshake").submit();
  };
  window.addEventListener("message", receiveHandshake);
  // This first discovery message carries no token, state, or user data.
  if (window.opener) window.opener.postMessage(message, "*");
})();
</script></body></html>
