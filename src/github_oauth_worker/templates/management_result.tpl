<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>{{ heading }}</title></head>
<body>
<main>
<h1>{{ heading }}</h1>
<p>{{ message }}</p>
{% if diagnostic_code %}<p>Troubleshooting code: <code>{{ diagnostic_code }}</code></p>{% endif %}
</main>
</body>
</html>
