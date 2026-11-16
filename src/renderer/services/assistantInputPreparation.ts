/** Static numeric-program documentation only: no source paths, pixels, execution or permissions. */
export function inputPreparationContract(task: string | null) {
  return {
    action: 'configure_input_recipe',
    language: 'modu-numeric-v1',
    supported_tasks: ['classification', 'segmentation'],
    supported_for_current_task: task === 'classification' || task === 'segmentation',
    operation: {op: 'code', language: 'modu-numeric-v1', code: 'result = image'},
    input: {name: 'image', dtype: 'float64', shape: 'H,W,3 RGB', range: [0, 255]},
    output: {name: 'result', shape: 'same as image', range: [0, 255],
      conversion: 'finite values required; clip to 0..255, round, then uint8'},
    limits: {code_utf8_bytes: 8192, lines: 32, input_pixels: 1048576, fft_calls: 4, ast_nodes: 256, ast_depth: 24, variables: 16, array_elements: 64 * 1024 * 1024, result_bytes: 512 * 1024 * 1024, work_bytes: 256 * 1024 * 1024, cooperative_elapsed_seconds: 5},
    hard_os_deadline: false,
    execution_order: 'Code runs before the final model resize. For larger original images, put an explicit resize or crop operation before the code block.',
    syntax: 'Assignments, finite numeric constants, arithmetic and comparisons, and named numeric functions only. This is a restricted DSL, not Python or JavaScript.',
    functions: ['fft2', 'ifft2', 'fftshift', 'ifftshift', 'notch_mask', 'abs', 'real', 'clip', 'minimum', 'maximum', 'sqrt', 'log', 'exp', 'where'],
    signatures: {
      fft2: 'fft2(array): spatial Fourier transform on axes (0,1); channels stay separate',
      ifft2: 'ifft2(array): requires an unshifted spectrum; inverse spatial Fourier transform on axes (0,1)',
      fftshift: 'fftshift(array): requires an unshifted spectrum; center spatial frequency axes (0,1), not spatial pixels',
      ifftshift: 'ifftshift(array): requires a shifted spectrum or notch filter; undo spatial frequency centering on axes (0,1)',
      abs: 'abs(array)', real: 'real(array)', clip: 'clip(array, low, high)',
      minimum: 'minimum(a, b)', maximum: 'maximum(a, b)', sqrt: 'sqrt(array)',
      log: 'log(array)', exp: 'exp(array)', where: 'where(condition, when_true, when_false)',
    },
    fft_axes: [0, 1],
    notch_mask: {signature: 'notch_mask(image, fx=.125, fy=0, radius=.02)',
      frequency_units: 'normalized cycles per pixel', frequency_range: [-0.5, 0.5], radius_range: [0.001, 0.25], preserves_dc: true},
    forbidden: ['imports', 'attributes', 'file or network access', 'system calls', 'eval', 'exec', 'loops', 'arbitrary Python or JavaScript'],
    fft_example: {
      code: 'spectrum = fftshift(fft2(image))\nmask = notch_mask(image, fx=.125, fy=0, radius=.02)\nresult = real(ifft2(ifftshift(spectrum * mask)))',
      quality_verified: false,
      notice: 'Example starting frequency only. Noise reduction or defect preservation is not verified; compare actual images before saving. Do not fit parameters using validation/test images.',
    },
    workflow: 'Read this contract, write code in input_recipe.operations, then propose_task with configure_input_recipe. The existing executor validates the full recipe, previews actual local pixels, checks recipe/source hashes and output size, and saves only in the same project/source/labelset/API scope. Do not claim a proposed program was run or saved until its receipt confirms it. Saving does not start training or approve quality.',
  };
}
