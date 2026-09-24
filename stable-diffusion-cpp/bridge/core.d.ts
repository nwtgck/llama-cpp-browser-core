/** Stable Diffusion browser bridge ABI 1. One serial request per instance. */
export interface StableDiffusionCore {
  HEAPU8: Uint8Array;
  FS: {
    mkdir(path: string): unknown;
    mount(type: unknown, options: { blobs: { name: string; data: Blob }[] }, path: string): unknown;
    unmount(path: string): unknown;
  };
  WORKERFS: unknown;
  ccall(name: 'sdb_load' | 'sdb_generate', result: 'number', types: ['string'], args: [string], options: { async: true }): Promise<number>;
  _sdb_abi_version(): number;
  _sdb_error(): number;
  _sdb_model_version(): number;
  _sdb_image_data(): number;
  _sdb_image_width(): number;
  _sdb_image_height(): number;
  _sdb_release_image(): void;
  _sdb_unload(): void;
  UTF8ToString(pointer: number): string;
}
export interface CoreOptions {
  wasmBinary: Uint8Array;
  locateFile(path: string): string;
  print?(text: string): void;
  printErr?(text: string): void;
  onAbort?(reason: unknown): void;
  onProgress?(step: number, steps: number, seconds: number): void;
  onLog?(level: number, text: string): void;
}
export default function createStableDiffusionCore(options: CoreOptions): Promise<StableDiffusionCore>;
