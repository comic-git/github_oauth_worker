<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Authorization complete</title></head>
<body><script>
(() => {
  const targetOrigin = {{ target_origin | tojson }};
  const message = {{ message | tojson }};
  if (window.opener) window.opener.postMessage(message, targetOrigin);
  window.close();
})();
</script></body></html>
