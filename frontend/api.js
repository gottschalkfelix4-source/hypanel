const response = await fetch('/openapi.json');
const schema = await response.json();
const body = document.querySelector('#endpoints');
for (const [path, methods] of Object.entries(schema.paths)) {
  for (const [method, operation] of Object.entries(methods)) {
    const tr = document.createElement('tr');
    for (const value of [method.toUpperCase(), path, operation.summary || '']) {
      const td = document.createElement('td'); td.textContent = value; tr.append(td);
    }
    body.append(tr);
  }
}
