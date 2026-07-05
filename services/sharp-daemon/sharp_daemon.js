const net = require('net');
const sharp = require('sharp');
const fs = require('fs');
const path = require('path');

const port = process.argv[2] !== undefined ? parseInt(process.argv[2], 10) : 0;

const server = net.createServer((socket) => {
    let buffer = '';

    socket.on('data', async (data) => {
        buffer += data.toString();
        if (buffer.includes('\n')) {
            const lines = buffer.split('\n');
            buffer = lines.pop(); // Keep partial line in buffer

            for (const line of lines) {
                if (!line.trim()) continue;

                let request;
                try {
                    request = JSON.parse(line);

                    if (request.ping) {
                        socket.write(JSON.stringify({ success: true, pong: true }) + '\n');
                        continue;
                    }

                    const traceId = request.trace_id || "system-daemon";
                    console.error(JSON.stringify({
                        "log.level": "INFO",
                        "trace.id": traceId,
                        "message": `sharp convert ${request.format}`
                    }));

                    const { inputPath, outputPath, format, quality } = request;

                    if (!inputPath || !outputPath) {
                        socket.write(JSON.stringify({ success: false, error: 'Missing paths', inputPath }) + '\n');
                        continue;
                    }

                    const startTime = Date.now();

                    let pipeline = sharp(inputPath).withMetadata();

                    if (format === 'webp') {
                        pipeline = pipeline.webp({ quality: parseInt(quality) });
                    } else if (format === 'avif') {
                        pipeline = pipeline.avif({ quality: parseInt(quality) });
                    } else if (format === 'jxl') {
                        // Sharp supports JXL if built with libjxl
                        pipeline = pipeline.jxl({
                            quality: parseFloat(quality),
                            lossless: parseFloat(quality) >= 100
                        });
                    } else if (format === 'jpeg' || format === 'jpg') {
                        pipeline = pipeline.jpeg({ quality: parseInt(quality) });
                    } else if (format === 'png') {
                        pipeline = pipeline.png({ quality: parseInt(quality) });
                    }

                    await pipeline.toFile(outputPath);

                    const duration_ms = Date.now() - startTime;
                    socket.write(JSON.stringify({
                        success: true,
                        duration_ms,
                        format,
                        quality,
                        inputPath
                    }) + '\n');

                } catch (err) {
                    socket.write(JSON.stringify({
                        success: false,
                        error: err.message,
                        inputPath: request ? request.inputPath : undefined
                    }) + '\n');
                }
            }
        }
    });

    socket.on('error', (err) => {
        console.error('Socket error:', err);
    });
});

server.listen(port, '127.0.0.1', () => {
    const actualPort = server.address().port;
    console.log(`PORT:${actualPort}`);
    console.log(`Sharp daemon listening on port ${actualPort}`);
});

// Auto-exit if stdin is closed (parent process died)
process.stdin.resume();
process.stdin.on('end', () => {
    process.exit(0);
});
