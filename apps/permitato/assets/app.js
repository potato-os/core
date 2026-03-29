"use strict";

let _styleLink = null;
let _permitatoModule = null;

export async function init(container, shellApi) {
  const res = await fetch("/app/permitato/assets/permitato.html");
  container.innerHTML = await res.text();

  _styleLink = document.createElement("link");
  _styleLink.rel = "stylesheet";
  _styleLink.href = "/app/permitato/assets/permitato.css";
  document.head.appendChild(_styleLink);

  _permitatoModule = await import("/app/permitato/assets/permitato.js");
  _permitatoModule.init(shellApi);
}

export function destroy() {
  if (_permitatoModule && typeof _permitatoModule.destroy === "function") {
    _permitatoModule.destroy();
  }
  if (_styleLink) {
    _styleLink.remove();
    _styleLink = null;
  }
  _permitatoModule = null;
}
