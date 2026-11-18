const style = document.createElement("style");
style.textContent = `
body.presenova-speech-ui main button {
  border-radius: 14px;
  padding: 12px 22px;
  font-weight: 600;
  transition: transform .2s, box-shadow .2s;
}
body.presenova-speech-ui main button:hover:not(:disabled) {
  transform: translateY(-2px);
  box-shadow: 0 8px 22px rgba(79,70,229,.20);
}
body.presenova-speech-ui main button:focus-visible {
  outline: 3px solid #818cf8;
  outline-offset: 3px;
}
body.presenova-speech-ui main button:disabled {
  opacity: .55;
  cursor: not-allowed;
}
`;
document.head.appendChild(style);

const sync = () => {
  document.body.classList.toggle(
    "presenova-speech-ui",
    location.pathname.toLowerCase().includes("speech")
  );
};
sync();
new MutationObserver(sync).observe(document.getElementById("root") || document.body, {
  childList: true,
  subtree: true
});
