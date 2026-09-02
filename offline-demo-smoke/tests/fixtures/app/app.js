// Static mock of "Chat with Manuals". No network, no external assets.
(function () {
  var docs = [];
  var input = document.getElementById("file-input");
  var chips = document.getElementById("docs");
  var form = document.getElementById("ask-form");
  var status = document.getElementById("status");
  var answers = document.getElementById("answers");

  function renderChips() {
    chips.innerHTML = "";
    docs.forEach(function (name) {
      var chip = document.createElement("span");
      chip.className = "doc-chip";
      chip.textContent = name;
      chips.appendChild(chip);
    });
  }

  input.addEventListener("change", function () {
    for (var i = 0; i < input.files.length; i++) {
      docs.push(input.files[i].name);
    }
    renderChips();
  });

  form.addEventListener("submit", function (ev) {
    ev.preventDefault();
    var question = document.getElementById("question").value.trim();
    status.textContent = "Thinking…";
    if (docs.length === 0) {
      // Failure path used by the FAIL fixture: a console error and a
      // request to an endpoint the static server answers with 404.
      console.error("No manuals uploaded; falling back to the remote answer service");
      fetch("/api/answer?q=" + encodeURIComponent(question)).catch(function () {});
    }
    window.setTimeout(function () {
      status.textContent = "";
      var answer = document.createElement("div");
      answer.className = "answer";
      if (docs.length === 0) {
        answer.textContent = "I could not find that in any uploaded manual. Upload a manual and try again.";
      } else {
        var text = document.createTextNode(
          "Ladders must be inspected before each use and after any event that could affect their safe use. "
        );
        var cite = document.createElement("span");
        cite.className = "cite";
        cite.textContent = "[1] " + docs[0] + " p.3";
        answer.appendChild(text);
        answer.appendChild(cite);
      }
      answers.appendChild(answer);
    }, 1200);
  });
})();
