<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Authorizing</title></head>
<body><script>
(() => {
  const message = "authorizing:github";
  const endpoint = {{ endpoint | tojson }};
  const receiveHandshake = async (event) => {
    if (event.source !== window.opener || event.data !== message) return;
    window.removeEventListener("message", receiveHandshake);
    try {
      const response = await fetch(endpoint, {
        method: "POST",
        credentials: "same-origin",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({origin: event.origin}),
      });
      document.body.textContent = await response.text();
    } catch {
      document.body.textContent = "Authorization could not be started.";
    }
  };
  window.addEventListener("message", receiveHandshake);
  // This first discovery message carries no token, state, or user data.
  if (window.opener) window.opener.postMessage(message, "*");
})();
</script></body></html>
