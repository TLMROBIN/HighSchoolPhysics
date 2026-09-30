(function () {
  "use strict";

  function renderQuestionMath(root) {
    if (!root || typeof window.renderMathInElement !== "function") return;
    window.renderMathInElement(root, {
      delimiters: [
        { left: "$$", right: "$$", display: true },
        { left: "\\[", right: "\\]", display: true },
        { left: "\\(", right: "\\)", display: false },
        { left: "$", right: "$", display: false }
      ],
      ignoredTags: ["script", "noscript", "style", "textarea", "pre", "code", "option"],
      throwOnError: false,
      strict: "warn",
      trust: false,
      macros: {}
    });
  }

  window.HSPRenderQuestionMath = renderQuestionMath;
  document.addEventListener("DOMContentLoaded", function () {
    renderQuestionMath(document.querySelector("main") || document.body);
  });
})();
