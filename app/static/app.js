/* OmniVoice Studio — comportamentos de cliente.
 *
 * Nenhuma dependência além do htmx: troca de modo, contador de caracteres,
 * gravação pelo microfone e datas relativas.
 */
(function () {
  "use strict";

  // ----------------------------------------------------------- modo ativo

  function syncModePanels() {
    var checked = document.querySelector('input[name="mode"]:checked');
    if (!checked) return;
    document.querySelectorAll("[data-mode-panel]").forEach(function (panel) {
      var active = panel.dataset.modePanel === checked.value;
      panel.hidden = !active;
      // Campos escondidos não devem bloquear o envio nem viajar no POST.
      panel.querySelectorAll("select, input").forEach(function (input) {
        input.disabled = !active;
      });
    });
  }

  // ------------------------------------------------- contador de caracteres

  function syncCharCount() {
    var textarea = document.getElementById("gen-text");
    var counter = document.getElementById("char-count");
    if (textarea && counter) counter.textContent = textarea.value.length;
  }

  // -------------------------------------------------------- datas relativas

  function relativeTime(seconds) {
    var delta = Date.now() / 1000 - seconds;
    if (delta < 60) return "agora";
    if (delta < 3600) return Math.floor(delta / 60) + " min atrás";
    if (delta < 86400) return Math.floor(delta / 3600) + " h atrás";
    if (delta < 604800) return Math.floor(delta / 86400) + " d atrás";
    return new Date(seconds * 1000).toLocaleDateString("pt-BR");
  }

  function syncTimestamps(root) {
    (root || document)
      .querySelectorAll("[data-timestamp]")
      .forEach(function (element) {
        var value = parseFloat(element.dataset.timestamp);
        if (!isNaN(value)) element.textContent = relativeTime(value);
      });
  }


  // --------------------------------------------------- codificação em WAV

  /* Converte um AudioBuffer em WAV PCM 16 bits mono. Gravar em webm/opus e
     mandar assim exigiria ffmpeg no servidor; convertendo aqui, o upload é
     sempre um formato que qualquer backend lê. */
  function encodeWav(audioBuffer) {
    var channels = audioBuffer.numberOfChannels;
    var length = audioBuffer.length;
    var mono = new Float32Array(length);

    for (var c = 0; c < channels; c++) {
      var data = audioBuffer.getChannelData(c);
      for (var i = 0; i < length; i++) mono[i] += data[i] / channels;
    }

    var buffer = new ArrayBuffer(44 + length * 2);
    var view = new DataView(buffer);
    var rate = audioBuffer.sampleRate;

    function writeString(offset, text) {
      for (var n = 0; n < text.length; n++) {
        view.setUint8(offset + n, text.charCodeAt(n));
      }
    }

    writeString(0, "RIFF");
    view.setUint32(4, 36 + length * 2, true);
    writeString(8, "WAVEfmt ");
    view.setUint32(16, 16, true); // tamanho do bloco fmt
    view.setUint16(20, 1, true); // PCM
    view.setUint16(22, 1, true); // mono
    view.setUint32(24, rate, true);
    view.setUint32(28, rate * 2, true); // bytes por segundo
    view.setUint16(32, 2, true); // alinhamento do bloco
    view.setUint16(34, 16, true); // bits por amostra
    writeString(36, "data");
    view.setUint32(40, length * 2, true);

    for (var s = 0; s < length; s++) {
      var value = Math.max(-1, Math.min(1, mono[s]));
      view.setInt16(44 + s * 2, value * 32767, true);
    }
    return new Blob([buffer], { type: "audio/wav" });
  }

  async function toWav(blob) {
    var Context = window.AudioContext || window.webkitAudioContext;
    if (!Context) return null;
    var context = new Context();
    try {
      var decoded = await context.decodeAudioData(await blob.arrayBuffer());
      return encodeWav(decoded);
    } catch (error) {
      return null; // navegador não decodificou: mandamos o original
    } finally {
      context.close();
    }
  }


  // ------------------------------------------------ editor de audiobook

  var Editor = {
    textarea: function () {
      return document.getElementById("gen-text");
    },

    /* Aplica a tag ao trecho selecionado — ou insere no cursor quando não há
       seleção. Uma tag de emoção vale do ponto onde entra até a próxima tag
       ou pausa, então colocá-la antes do trecho é o bastante. */
    applyTag: function (tag) {
      var field = this.textarea();
      if (!field) return;

      var start = field.selectionStart || 0;
      var end = field.selectionEnd || 0;
      var value = field.value;
      var isPause = /^\[(pause|long-pause)/.test(tag);
      var caret;

      if (!isPause && end > start) {
        // Emoção com seleção: marca o começo do trecho escolhido.
        var prefix = value.slice(0, start).replace(/[ \t]+$/, "");
        if (prefix && !/\n$/.test(prefix)) prefix += " ";
        var body = value.slice(start, end).trim();
        field.value = prefix + tag + " " + body + value.slice(end);
        caret = (prefix + tag + " " + body).length;
      } else {
        // Sem seleção (ou pausa): entra no cursor, em linha própria.
        var before = value.slice(0, end).replace(/[ \t]+$/, "");
        var after = value.slice(end);
        var separator = isPause ? "\n" : " ";
        var lead = before && !/\n$/.test(before) ? separator : "";
        var trail = after && !/^\s/.test(after) ? separator : "";
        field.value = before + lead + tag + trail + after;
        caret = (before + lead + tag).length;
      }

      field.focus();
      field.setSelectionRange(caret, caret);
      this.changed();
    },

    changed: function () {
      syncCharCount();
      document.body.dispatchEvent(new CustomEvent("markup-changed", { bubbles: true }));
    },

    /* Trecho para "Testar trecho": a seleção, ou o começo do texto. */
    excerpt: function (limit) {
      var field = this.textarea();
      if (!field) return "";
      var selected = field.value.slice(field.selectionStart, field.selectionEnd).trim();
      var text = selected || field.value.trim();
      if (text.length <= limit) return text;
      // Corta num limite de frase para a prévia não terminar no meio.
      var cut = text.slice(0, limit);
      var stop = Math.max(cut.lastIndexOf("."), cut.lastIndexOf("\n"), cut.lastIndexOf("!"));
      return (stop > limit * 0.4 ? cut.slice(0, stop + 1) : cut).trim();
    },
  };

  window.OmniEditor = Editor;

  // ------------------------------------------------------------- gravação

  var Recorder = {
    media: null,
    chunks: [],
    stream: null,
    timer: null,
    startedAt: 0,

    supported: function () {
      return !!(
        navigator.mediaDevices &&
        navigator.mediaDevices.getUserMedia &&
        window.MediaRecorder
      );
    },

    reset: function () {
      var preview = document.getElementById("rec-preview");
      if (preview) {
        if (preview.src) URL.revokeObjectURL(preview.src);
        preview.removeAttribute("src");
        preview.hidden = true;
      }
      this.setLabel("Gravar pelo microfone", false);
      var timer = document.getElementById("rec-timer");
      if (timer) timer.hidden = true;
    },

    setLabel: function (text, recording) {
      var button = document.getElementById("rec-toggle");
      var label = document.getElementById("rec-label");
      var dot = button && button.querySelector(".rec-dot");
      if (label) label.textContent = text;
      if (button) button.classList.toggle("is-recording", !!recording);
      if (dot) dot.hidden = !recording;
    },

    tick: function () {
      var timer = document.getElementById("rec-timer");
      if (!timer) return;
      var elapsed = Math.floor((Date.now() - this.startedAt) / 1000);
      timer.hidden = false;
      timer.textContent =
        Math.floor(elapsed / 60) + ":" + String(elapsed % 60).padStart(2, "0");
    },

    start: async function () {
      try {
        this.stream = await navigator.mediaDevices.getUserMedia({
          audio: {
            echoCancellation: true,
            noiseSuppression: true,
            autoGainControl: true,
          },
        });
      } catch (error) {
        alert(
          "Não foi possível acessar o microfone. Verifique a permissão do " +
            "navegador.\n\nEm celulares, o microfone só funciona em HTTPS ou " +
            "em http://localhost."
        );
        return;
      }
      this.chunks = [];
      this.media = new MediaRecorder(this.stream);
      this.media.ondataavailable = function (event) {
        if (event.data && event.data.size) Recorder.chunks.push(event.data);
      };
      this.media.onstop = function () {
        Recorder.finish();
      };
      this.media.start();
      this.startedAt = Date.now();
      this.tick();
      this.timer = setInterval(this.tick.bind(this), 500);
      this.setLabel("Parar gravação", true);
    },

    stop: function () {
      if (this.media && this.media.state !== "inactive") this.media.stop();
      clearInterval(this.timer);
    },

    finish: async function () {
      var type = (this.media && this.media.mimeType) || "audio/webm";
      var recorded = new Blob(this.chunks, { type: type });

      this.stream.getTracks().forEach(function (track) {
        track.stop();
      });
      this.setLabel("Convertendo…", false);

      var wav = await toWav(recorded);
      var blob = wav || recorded;
      var name = wav
        ? "gravacao.wav"
        : "gravacao." +
          (type.indexOf("mp4") >= 0 ? "mp4" : type.indexOf("ogg") >= 0 ? "ogg" : "webm");

      var input = document.getElementById("voice-audio");
      if (input) {
        var transfer = new DataTransfer();
        transfer.items.add(new File([blob], name, { type: blob.type }));
        input.files = transfer.files;
      }

      var preview = document.getElementById("rec-preview");
      if (preview) {
        if (preview.src) URL.revokeObjectURL(preview.src);
        preview.src = URL.createObjectURL(blob);
        preview.hidden = false;
      }

      this.setLabel("Gravar de novo", false);
    },

    toggle: function () {
      if (this.media && this.media.state === "recording") this.stop();
      else this.start();
    },
  };

  window.OmniRecorder = Recorder;


  function togglePausePanel(force) {
    var panel = document.getElementById("pause-panel");
    var button = document.getElementById("pause-toggle");
    if (!panel || !button) return;
    var open = force === undefined ? panel.hidden : force;
    panel.hidden = !open;
    button.setAttribute("aria-expanded", String(open));
    button.classList.toggle("is-active", open);
  }

  function saveProject() {
    var field = document.getElementById("gen-text");
    var nameInput = document.getElementById("project-name");
    if (!field || !field.value.trim()) {
      alert("Escreva algum texto antes de salvar.");
      return;
    }
    var name = (nameInput && nameInput.value.trim()) || "";
    if (!name) {
      name = prompt("Nome do capítulo:", "Capítulo 1") || "";
      if (!name.trim()) return;
    }
    htmx
      .ajax("POST", "/projetos", {
        target: "#project-list",
        swap: "innerHTML",
        values: { name: name.trim(), text: field.value },
      })
      .then(function () {
        if (nameInput) nameInput.value = "";
      });
  }

  async function openProject(id) {
    var response = await fetch("/projetos/" + id);
    if (!response.ok) return;
    var project = await response.json();
    var field = document.getElementById("gen-text");
    if (!field) return;
    if (field.value.trim() && !confirm("Substituir o texto atual por “" + project.name + "”?")) {
      return;
    }
    field.value = project.text;
    var nameInput = document.getElementById("project-name");
    if (nameInput) nameInput.value = project.name;
    Editor.changed();
    field.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  // ------------------------------------------------------------- ligações

  function wire() {
    syncModePanels();
    syncCharCount();
    syncTimestamps();
    if (document.getElementById("markup-status")) Editor.changed();

    var recordButton = document.getElementById("rec-toggle");
    if (recordButton && !Recorder.supported()) {
      recordButton.disabled = true;
      document.getElementById("rec-label").textContent =
        "Gravação indisponível aqui";
      // Navegadores só liberam o microfone em HTTPS ou em localhost.
      var reason = window.isSecureContext
        ? "Este navegador não suporta gravação. Envie um arquivo de áudio."
        : "O microfone só funciona em HTTPS ou em http://localhost. " +
          "Num endereço de rede local, envie um arquivo de áudio.";
      recordButton.title = reason;
      var note = document.createElement("p");
      note.className = "hint";
      note.textContent = reason;
      recordButton.parentElement.insertAdjacentElement("afterend", note);
    }

    // Pré-seleciona a voz vinda de /?voz=<id> (botão "Usar" da biblioteca).
    var wanted = new URLSearchParams(location.search).get("voz");
    if (wanted) {
      var cloneMode = document.getElementById("mode-clone");
      if (cloneMode && !cloneMode.checked) {
        cloneMode.checked = true;
        syncModePanels();
      }
      var select = document.getElementById("voice-select");
      if (select && select.querySelector('option[value="' + wanted + '"]')) {
        select.value = wanted;
      }
    }
  }

  document.addEventListener("change", function (event) {
    if (event.target.name === "mode") syncModePanels();
  });

  document.addEventListener("input", function (event) {
    if (event.target.id === "gen-text") Editor.changed();
  });

  /* "Testar trecho" reaproveita o formulário inteiro (voz, idioma, ajustes)
     mas troca o texto pelo recorte selecionado. */
  document.body.addEventListener("htmx:configRequest", function (event) {
    if (event.detail.path !== "/testar-trecho") return;
    var limit = parseInt(
      document.getElementById("preview-submit").dataset.limit || "600",
      10
    );
    var excerpt = Editor.excerpt(limit);
    if (!excerpt) {
      event.preventDefault();
      alert("Escreva ou selecione um trecho para testar.");
      return;
    }
    event.detail.parameters.text = excerpt;
  });

  document.addEventListener("click", function (event) {
    var recordButton = event.target.closest("#rec-toggle");
    if (recordButton) {
      event.preventDefault();
      Recorder.toggle();
      return;
    }

    var chip = event.target.closest("[data-tag]");
    if (chip) {
      event.preventDefault();
      Editor.applyTag(chip.dataset.tag);
      var panel = document.getElementById("pause-panel");
      if (panel && panel.contains(chip)) togglePausePanel(false);
      return;
    }

    if (event.target.closest("#pause-toggle")) {
      event.preventDefault();
      togglePausePanel();
      return;
    }

    if (event.target.closest("#pause-custom")) {
      event.preventDefault();
      var answer = prompt("Pausa em segundos (ex.: 3.5):", "3");
      var seconds = parseFloat((answer || "").replace(",", "."));
      if (!isNaN(seconds) && seconds > 0) {
        Editor.applyTag("[pause=" + seconds + "]");
        togglePausePanel(false);
      }
      return;
    }

    if (event.target.closest("[data-close-help]")) {
      event.preventDefault();
      document.getElementById("markup-help").innerHTML = "";
      return;
    }

    if (event.target.closest("#project-save")) {
      event.preventDefault();
      saveProject();
      return;
    }

    var loadProject = event.target.closest("[data-load-project]");
    if (loadProject) {
      event.preventDefault();
      openProject(loadProject.dataset.loadProject);
      return;
    }

    var renameButton = event.target.closest("[data-rename]");
    if (renameButton) {
      event.preventDefault();
      var name = prompt("Novo nome da voz:", renameButton.dataset.currentName);
      if (name && name.trim()) {
        htmx.ajax("POST", "/vozes/" + renameButton.dataset.rename + "/renomear", {
          target: "#voice-list",
          swap: "innerHTML",
          values: { name: name.trim() },
        });
      }
    }
  });

  document.addEventListener("DOMContentLoaded", wire);
  document.body.addEventListener("htmx:afterSwap", function (event) {
    syncTimestamps(event.target);
    // O seletor de vozes pode ter sido recarregado dentro do painel oculto.
    syncModePanels();
  });

  setInterval(function () {
    syncTimestamps();
  }, 60000);
})();
