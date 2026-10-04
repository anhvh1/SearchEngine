// Starts Swagger UI from a file rather than an inline script, so the API page works under the backend's
// Content-Security-Policy (script-src 'self').
window.ui = SwaggerUIBundle({
  url: '/openapi.json',
  dom_id: '#swagger-ui',
  layout: 'BaseLayout',
  deepLinking: true,
  persistAuthorization: true,
  tryItOutEnabled: true,
  presets: [SwaggerUIBundle.presets.apis, SwaggerUIBundle.SwaggerUIStandalonePreset],
});
