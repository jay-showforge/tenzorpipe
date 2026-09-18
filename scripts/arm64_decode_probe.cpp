// Minimal OpenH264 decoder harness used by scripts/check_arm64_openh264.sh.
//
// Decodes an Annex-B stream and prints the frame count, the decode time and a
// checksum over every output picture's cropped YUV planes. Building it for two
// architectures and comparing checksums proves the vendored decoder — and in
// particular its per-architecture assembly — produces identical pictures.
// H.264 decoding is bit-exact by specification, so any difference is a defect.
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cstdint>
#include <vector>
#include <ctime>
#include "codec_api.h"

// FNV-1a over the emitted planes: no external dependency, and a mismatch anywhere
// changes the digest.
struct Fnv {
  uint64_t h = 1469598103934665603ULL;
  void feed(const uint8_t* p, size_t n) {
    for (size_t i = 0; i < n; i++) {
      h ^= p[i];
      h *= 1099511628211ULL;
    }
  }
};

int main(int argc, char** argv) {
  if (argc < 2) {
    fprintf(stderr, "usage: arm64_decode_probe <stream.h264>\n");
    return 2;
  }
  FILE* f = fopen(argv[1], "rb");
  if (!f) {
    perror("open");
    return 2;
  }
  fseek(f, 0, SEEK_END);
  long size = ftell(f);
  fseek(f, 0, SEEK_SET);
  std::vector<uint8_t> data(size);
  if (fread(data.data(), 1, size, f) != (size_t)size) {
    perror("read");
    return 2;
  }
  fclose(f);

  ISVCDecoder* decoder = nullptr;
  if (WelsCreateDecoder(&decoder) != 0 || decoder == nullptr) {
    fprintf(stderr, "WelsCreateDecoder failed\n");
    return 3;
  }
  SDecodingParam param;
  memset(&param, 0, sizeof(param));
  param.sVideoProperty.eVideoBsType = VIDEO_BITSTREAM_AVC;
  param.eEcActiveIdc = ERROR_CON_DISABLE;
  if (decoder->Initialize(&param) != 0) {
    fprintf(stderr, "Initialize failed\n");
    return 3;
  }

  Fnv digest;
  long frames = 0;
  // Split Annex-B into access units at every start code that begins a new picture;
  // feeding one NAL at a time is enough for this harness.
  std::vector<size_t> starts;
  for (size_t i = 0; i + 3 <= data.size(); i++) {
    if (data[i] == 0 && data[i + 1] == 0 && data[i + 2] == 1) {
      size_t begin = (i > 0 && data[i - 1] == 0) ? i - 1 : i;
      if (starts.empty() || starts.back() != begin) starts.push_back(begin);
    }
  }
  starts.push_back(data.size());

  struct timespec t0, t1;
  clock_gettime(CLOCK_MONOTONIC, &t0);
  uint8_t* planes[3] = {nullptr, nullptr, nullptr};
  SBufferInfo info;
  auto collect = [&]() {
    if (info.iBufferStatus != 1) return;
    int w = info.UsrData.sSystemBuffer.iWidth;
    int h = info.UsrData.sSystemBuffer.iHeight;
    int sy = info.UsrData.sSystemBuffer.iStride[0];
    int sc = info.UsrData.sSystemBuffer.iStride[1];
    for (int y = 0; y < h; y++) digest.feed(planes[0] + (size_t)y * sy, w);
    for (int y = 0; y < h / 2; y++) digest.feed(planes[1] + (size_t)y * sc, w / 2);
    for (int y = 0; y < h / 2; y++) digest.feed(planes[2] + (size_t)y * sc, w / 2);
    frames++;
  };
  for (size_t i = 0; i + 1 < starts.size(); i++) {
    memset(&info, 0, sizeof(info));
    planes[0] = planes[1] = planes[2] = nullptr;
    size_t begin = starts[i], end = starts[i + 1];
    if (decoder->DecodeFrameNoDelay(data.data() + begin, (int)(end - begin), planes, &info) != 0) {
      fprintf(stderr, "decode error at offset %zu\n", begin);
      decoder->Uninitialize();
      WelsDestroyDecoder(decoder);
      return 4;
    }
    collect();
  }
  // Flush.
  for (;;) {
    memset(&info, 0, sizeof(info));
    planes[0] = planes[1] = planes[2] = nullptr;
    int32_t end_of_stream = 1;
    decoder->SetOption(DECODER_OPTION_END_OF_STREAM, &end_of_stream);
    if (decoder->FlushFrame(planes, &info) != 0 || info.iBufferStatus != 1) break;
    collect();
  }
  clock_gettime(CLOCK_MONOTONIC, &t1);
  double seconds = (t1.tv_sec - t0.tv_sec) + (t1.tv_nsec - t0.tv_nsec) / 1e9;
  printf("frames=%ld digest=%016llx seconds=%.3f\n", frames,
         (unsigned long long)digest.h, seconds);
  decoder->Uninitialize();
  WelsDestroyDecoder(decoder);
  return 0;
}
