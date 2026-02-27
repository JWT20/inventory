const Camera = (() => {
    let stream = null;
    let videoEl = null;
    let canvasEl = null;

    async function start(video, canvas) {
        videoEl = video;
        canvasEl = canvas;

        const constraints = {
            video: {
                facingMode: { ideal: 'environment' },
                width: { ideal: 1280 },
                height: { ideal: 960 },
            },
        };

        stream = await navigator.mediaDevices.getUserMedia(constraints);
        videoEl.srcObject = stream;
        await videoEl.play();
    }

    function stop() {
        if (stream) {
            stream.getTracks().forEach((t) => t.stop());
            stream = null;
        }
        if (videoEl) {
            videoEl.srcObject = null;
        }
    }

    function capture() {
        if (!videoEl || !canvasEl) return null;

        canvasEl.width = videoEl.videoWidth;
        canvasEl.height = videoEl.videoHeight;
        const ctx = canvasEl.getContext('2d');
        ctx.drawImage(videoEl, 0, 0);

        return new Promise((resolve) => {
            canvasEl.toBlob(resolve, 'image/jpeg', 0.85);
        });
    }

    return { start, stop, capture };
})();
