<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Continue setup</title></head>
<body><script>
(() => {
  const message = "authorizing:github";
  const receiveHandshake = async (event) => {
    if (event.source !== window.opener || event.data !== message) return;
    window.removeEventListener("message", receiveHandshake);
    const response = await fetch("/setup/handshake", {
      method: "POST", credentials: "same-origin",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({origin: event.origin, installation_id: {{ installation_id | tojson }}}),
    });
    document.body.textContent = await response.text();
  };
  window.addEventListener("message", receiveHandshake);
  // This initial discovery message has no token, state, or user data.
  if (window.opener) window.opener.postMessage(message, "*");
})();
</script></body></html>
