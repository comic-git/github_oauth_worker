<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>Choose comic repository</title></head>
<body>
<main>
  <h1>Choose comic repository</h1>
  {% if repositories %}
  <form action="/setup/select-discovered" method="post">
    <input type="hidden" name="state" value="{{ state_token }}">
    {% for installation_id, repository in repositories %}
    <p><label><input type="radio" name="selection" value="{{ installation_id }}:{{ repository.id }}" required> {{ repository.owner.login }}/{{ repository.name }}</label></p>
    {% endfor %}
    <p><button type="submit">Continue with selected repository</button></p>
  </form>
  {% else %}
  <p>No repositories are connected to this GitHub App for your account yet.</p>
  {% endif %}
  {% if install_url %}
  <p><a href="{{ install_url }}">Add repository access on GitHub</a></p>
  {% else %}
  <p>To connect another repository, ask the worker operator for the GitHub App installation link.</p>
  {% endif %}
  <form action="/setup/switch-account" method="post"><p><button type="submit">Use another GitHub account</button></p></form>
</main>
</body>
</html>
