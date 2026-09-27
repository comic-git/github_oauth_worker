<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>Enable comic_git CMS</title></head>
<body>
<main>
  <h1>Enable comic_git CMS</h1>
  <p>Continue to choose a repository and prepare its CMS configuration for review.</p>
  <form action="/setup/continue" method="post">
    <input type="hidden" name="installation_id" value="{{ installation_id }}">
    <button type="submit">Continue</button>
  </form>
  <form action="/setup/switch-account" method="post"><p><button type="submit">Use another GitHub account</button></p></form>
</main>
</body>
</html>
