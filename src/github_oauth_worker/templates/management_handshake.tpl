<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>{{ title }}</title></head>
<body><script>
(() => {
  const message = "authorizing:github";
  const endpoint = {{ endpoint | tojson }};
  const receiveHandshake = async (event) => {
    if (event.source !== window.opener || event.data !== message) return;
    window.removeEventListener("message", receiveHandshake);
    const requestBody = {origin: event.origin{% if target_origin %}, target_origin: {{ target_origin | tojson }}{% endif %}};
    try {
      const response = await fetch(endpoint, {
        method: "POST", credentials: "same-origin",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(requestBody),
      });
      document.body.textContent = await response.text();
    } catch {
      document.body.textContent = "CMS origin management could not be completed.";
    }
  };
  window.addEventListener("message", receiveHandshake);
  if (window.opener) window.opener.postMessage(message, "*");
})();
</script></body></html>
