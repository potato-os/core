"use strict";

import { appState, IMAGE_SAFE_MAX_BYTES, IMAGE_MAX_DIMENSION, IMAGE_MAX_PIXEL_COUNT } from "./state.js";
import { formatBytes, estimateDataUrlBytes } from "./utils.js";
import { appendMessage } from "./messages.js";

    let _ui = {};

    export function registerImageUiCallbacks(callbacks) {
      _ui = callbacks;
    }

    function dataUrlToImage(dataUrl) {
      return new Promise((resolve, reject) => {
        const img = new Image();
        img.onload = () => resolve(img);
        img.onerror = () => reject(new Error("image_decode_failed"));
        img.src = dataUrl;
      });
    }

    async function inspectImageDataUrl(dataUrl) {
      const image = await dataUrlToImage(dataUrl);
      const width = Math.max(1, Number(image.naturalWidth) || 1);
      const height = Math.max(1, Number(image.naturalHeight) || 1);
      return {
        width,
        height,
        maxDim: Math.max(width, height),
        pixelCount: width * height,
      };
    }

    function canvasToDataUrl(canvas, mimeType, quality) {
      return new Promise((resolve, reject) => {
        canvas.toBlob(
          (blob) => {
            if (!blob) {
              reject(new Error("canvas_blob_failed"));
              return;
            }
            const fr = new FileReader();
            fr.onload = () => resolve({ dataUrl: String(fr.result || ""), size: blob.size });
            fr.onerror = () => reject(new Error("canvas_read_failed"));
            fr.readAsDataURL(blob);
          },
          mimeType,
          quality
        );
      });
    }

    async function compressImageDataUrl(originalDataUrl) {
      const image = await dataUrlToImage(originalDataUrl);
      const maxDim = Math.max(image.naturalWidth || 1, image.naturalHeight || 1);
      const scale = maxDim > IMAGE_MAX_DIMENSION ? IMAGE_MAX_DIMENSION / maxDim : 1;
      const width = Math.max(1, Math.round((image.naturalWidth || 1) * scale));
      const height = Math.max(1, Math.round((image.naturalHeight || 1) * scale));
      const canvas = document.createElement("canvas");
      canvas.width = width;
      canvas.height = height;
      const ctx = canvas.getContext("2d");
      if (!ctx) {
        throw new Error("canvas_context_failed");
      }
      ctx.drawImage(image, 0, 0, width, height);

      const qualities = [0.82, 0.74, 0.66, 0.58, 0.5, 0.42];
      let best = null;
      for (const quality of qualities) {
        const candidate = await canvasToDataUrl(canvas, "image/jpeg", quality);
        if (!best || candidate.size < best.size) {
          best = candidate;
        }
        if (candidate.size <= IMAGE_SAFE_MAX_BYTES) {
          break;
        }
      }

      if (!best) {
        throw new Error("compress_failed");
      }

      return {
        dataUrl: best.dataUrl,
        size: best.size,
        type: "image/jpeg",
      };
    }

    async function maybeCompressImage(dataUrl, file) {
      const inputSize = Number(file?.size) || estimateDataUrlBytes(dataUrl);
      let metadata = null;
      try {
        metadata = await inspectImageDataUrl(dataUrl);
      } catch (_err) {
        metadata = null;
      }
      const needsResize = Boolean(
        metadata && (
          metadata.maxDim > IMAGE_MAX_DIMENSION
          || metadata.pixelCount > IMAGE_MAX_PIXEL_COUNT
        )
      );

      if (inputSize <= IMAGE_SAFE_MAX_BYTES && !needsResize) {
        return {
          dataUrl,
          size: inputSize,
          type: file?.type || "image/*",
          optimized: false,
          originalSize: inputSize,
        };
      }

      if (_ui.setComposerActivity) _ui.setComposerActivity("Optimizing image...");
      if (_ui.setComposerStatusChip) _ui.setComposerStatusChip("Optimizing image...", { phase: "image" });
      const compressed = await compressImageDataUrl(dataUrl);
      return {
        dataUrl: compressed.dataUrl,
        size: compressed.size,
        type: compressed.type,
        optimized: true,
        originalSize: inputSize,
      };
    }

    export function cancelPendingImageWork() {
      appState.pendingImageToken += 1;
      if (appState.pendingImageReader) {
        appState.pendingImageReader.abort();
      }
      appState.pendingImageReader = null;
    }

    export function clearPendingImage() {
      appState.pendingImage = null;
      const fileInput = document.getElementById("imageInput");
      const attachBtn = document.getElementById("attachImageBtn");
      const preview = document.getElementById("imagePreview");
      const previewWrap = document.getElementById("imagePreviewWrap");
      const imageMeta = document.getElementById("imageMeta");
      const clearBtn = document.getElementById("clearImageBtn");
      if (fileInput) {
        fileInput.value = "";
      }
      if (preview) {
        preview.removeAttribute("src");
      }
      if (previewWrap) {
        previewWrap.hidden = true;
      }
      if (imageMeta) {
        imageMeta.textContent = "";
        imageMeta.hidden = true;
      }
      if (clearBtn) {
        clearBtn.hidden = true;
      }
      if (attachBtn) {
        attachBtn.textContent = "Attach image";
        attachBtn.classList.remove("selected");
      }
    }

    export function handleImageSelected(file) {
      const selectionToken = appState.pendingImageToken + 1;
      appState.pendingImageToken = selectionToken;

      if (!file) {
        clearPendingImage();
        if (_ui.setComposerActivity) _ui.setComposerActivity("");
        if (_ui.hideComposerStatusChip) _ui.hideComposerStatusChip();
        if (_ui.setCancelEnabled) _ui.setCancelEnabled(false);
        if (_ui.focusPromptInput) _ui.focusPromptInput();
        return;
      }
      if (_ui.activeRuntimeVisionCapability && _ui.activeRuntimeVisionCapability(appState.latestStatus) === false) {
        clearPendingImage();
        if (_ui.showTextOnlyImageBlockedState) _ui.showTextOnlyImageBlockedState(appState.latestStatus);
        return;
      }
      if (!String(file.type || "").startsWith("image/")) {
        appendMessage("assistant", "Only image files are supported.");
        clearPendingImage();
        if (_ui.setComposerActivity) _ui.setComposerActivity("");
        if (_ui.hideComposerStatusChip) _ui.hideComposerStatusChip();
        if (_ui.setCancelEnabled) _ui.setCancelEnabled(false);
        if (_ui.focusPromptInput) _ui.focusPromptInput();
        return;
      }

      if (appState.pendingImageReader) {
        appState.pendingImageReader.abort();
      }
      const reader = new FileReader();
      appState.pendingImageReader = reader;
      if (_ui.setComposerActivity) _ui.setComposerActivity("Reading image...");
      if (_ui.setComposerStatusChip) _ui.setComposerStatusChip("Reading image • 0%", { phase: "image" });
      if (_ui.setCancelEnabled) _ui.setCancelEnabled(true);
      reader.onprogress = (event) => {
        if (event.lengthComputable && event.total > 0) {
          const percent = Math.round((event.loaded * 100) / event.total);
          if (_ui.setComposerActivity) _ui.setComposerActivity(`Reading image... ${percent}%`);
          if (_ui.setComposerStatusChip) _ui.setComposerStatusChip(`Reading image • ${percent}%`, { phase: "image" });
          return;
        }
        if (_ui.setComposerActivity) _ui.setComposerActivity("Reading image...");
        if (_ui.setComposerStatusChip) _ui.setComposerStatusChip("Reading image...", { phase: "image" });
      };
      reader.onload = async () => {
        if (selectionToken !== appState.pendingImageToken) {
          return;
        }
        const result = typeof reader.result === "string" ? reader.result : "";
        if (!result.startsWith("data:image/")) {
          appendMessage("assistant", "Invalid image encoding.");
          clearPendingImage();
          appState.pendingImageReader = null;
          if (_ui.setComposerActivity) _ui.setComposerActivity("");
          if (_ui.hideComposerStatusChip) _ui.hideComposerStatusChip();
          if (_ui.setCancelEnabled) _ui.setCancelEnabled(false);
          if (_ui.focusPromptInput) _ui.focusPromptInput();
          return;
        }

        let processedImage;
        try {
          processedImage = await maybeCompressImage(result, file);
        } catch (_err) {
          appendMessage("assistant", "Could not optimize the selected image.");
          clearPendingImage();
          appState.pendingImageReader = null;
          if (_ui.setComposerActivity) _ui.setComposerActivity("");
          if (_ui.hideComposerStatusChip) _ui.hideComposerStatusChip();
          if (_ui.setCancelEnabled) _ui.setCancelEnabled(false);
          if (_ui.focusPromptInput) _ui.focusPromptInput();
          return;
        }

        if (selectionToken !== appState.pendingImageToken) {
          return;
        }

        appState.pendingImage = {
          name: file.name || "image",
          type: processedImage.type || file.type || "image/*",
          size: Number(processedImage.size) || 0,
          originalSize: Number(processedImage.originalSize) || Number(file.size) || 0,
          optimized: Boolean(processedImage.optimized),
          dataUrl: processedImage.dataUrl || result,
        };

        const preview = document.getElementById("imagePreview");
        const previewWrap = document.getElementById("imagePreviewWrap");
        const imageMeta = document.getElementById("imageMeta");
        const clearBtn = document.getElementById("clearImageBtn");
        const attachBtn = document.getElementById("attachImageBtn");
        if (preview) {
          preview.src = appState.pendingImage.dataUrl;
        }
        if (previewWrap) {
          previewWrap.hidden = false;
        }
        if (imageMeta) {
          if (appState.pendingImage.optimized && appState.pendingImage.originalSize > appState.pendingImage.size) {
            imageMeta.textContent = `${appState.pendingImage.name} (${formatBytes(appState.pendingImage.size)}, optimized from ${formatBytes(appState.pendingImage.originalSize)})`;
          } else {
            imageMeta.textContent = `${appState.pendingImage.name} (${formatBytes(appState.pendingImage.size)})`;
          }
          imageMeta.hidden = false;
        }
        if (clearBtn) {
          clearBtn.hidden = false;
        }
        if (attachBtn) {
          attachBtn.textContent = "Change image";
          attachBtn.classList.add("selected");
        }
        appState.pendingImageReader = null;
        if (_ui.setComposerActivity) _ui.setComposerActivity("");
        if (_ui.hideComposerStatusChip) _ui.hideComposerStatusChip();
        if (_ui.setCancelEnabled) _ui.setCancelEnabled(false);
        if (_ui.focusPromptInput) _ui.focusPromptInput();
      };
      reader.onerror = () => {
        if (selectionToken !== appState.pendingImageToken) {
          return;
        }
        appendMessage("assistant", "Could not read the selected image.");
        clearPendingImage();
        appState.pendingImageReader = null;
        if (_ui.setComposerActivity) _ui.setComposerActivity("");
        if (_ui.hideComposerStatusChip) _ui.hideComposerStatusChip();
        if (_ui.setCancelEnabled) _ui.setCancelEnabled(false);
        if (_ui.focusPromptInput) _ui.focusPromptInput();
      };
      reader.onabort = () => {
        if (selectionToken !== appState.pendingImageToken) {
          return;
        }
        clearPendingImage();
        appState.pendingImageReader = null;
        if (_ui.setComposerActivity) _ui.setComposerActivity("Image load cancelled.");
        if (_ui.hideComposerStatusChip) _ui.hideComposerStatusChip();
        if (_ui.setCancelEnabled) _ui.setCancelEnabled(false);
        if (_ui.focusPromptInput) _ui.focusPromptInput();
      };
      reader.readAsDataURL(file);
    }

    export function buildUserMessageContent(content) {
      if (!appState.pendingImage) {
        return content;
      }
      const textPart = content || "Describe this image.";
      return [
        { type: "text", text: textPart },
        { type: "image_url", image_url: { url: appState.pendingImage.dataUrl } },
      ];
    }

    export function buildUserBubblePayload(content) {
      const text = String(content || "");
      if (!appState.pendingImage) {
        return {
          text,
          imageDataUrl: "",
          imageName: "",
        };
      }
      return {
        text,
        imageDataUrl: appState.pendingImage.dataUrl,
        imageName: appState.pendingImage.name || "image",
      };
    }

    export function openImagePicker() {
      if (appState.requestInFlight) return;
      if (_ui.activeRuntimeVisionCapability && _ui.activeRuntimeVisionCapability(appState.latestStatus) === false) {
        clearPendingImage();
        if (_ui.showTextOnlyImageBlockedState) _ui.showTextOnlyImageBlockedState(appState.latestStatus);
        return;
      }
      const input = document.getElementById("imageInput");
      if (!input) return;
      input.value = "";
      input.click();
    }
