# Real-ESRGAN Overview

[Real-ESRGAN](https://github.com/xinntao/Real-ESRGAN) is a package that uses machine learning for image restoration, including upscaling and cleaning up noisy images. Given that VQGAN+CLIP can only generate lower-resolution images (significantly limited by available VRAM), applying ESRGAN for upscaling can be useful. This document will discuss installation and application of ESRGAN in combination with VQGAN_CLIP_GENERATOR. In brief, Real-ESRGAN can be used after generating single images with VQGAN, or after generating video frames (and before encoding video) using any of the generate.*_video_frames() methods.


[Example images](https://github.com/xinntao/Real-ESRGAN/blob/master/README.md#book-real-esrgan-training-real-world-blind-super-resolution-with-pure-synthetic-data) are provided by [the Real-ESRGAN developer](https://github.com/xinntao/Real-ESRGAN).

## Setup
### Virtual environment
Install [Real-ESRGAN](https://github.com/xinntao/Real-ESRGAN) to the same virtual environment where you are running vqgan_clip_generator using the commands below.

```sh
conda activate vqgan
pip install opencv-python scipy
pip install basicsr
pip install facexlib
pip install gfpgan
pip install git+https://github.com/xinntao/Real-ESRGAN
```

### Dyamic download and caching of models

Real-ESRGAN requires use of a compatible model file. There are many compatible models that have been trained for many image restoration tasks. Using the same approach as VQGAN models, these Real-ESRGAN models are used by providing the name of the model file and the URL where it can be downloaded as arguments to the inference_realesrgan() method. The first time a model is used it will be downloaded and cached locally. Subsequent calls to the same model will use the cached copy. Caches are stored in the ~/.cache/torch/hub/models folder.

The model files provided by the developer of Real-ESRGAN are shown below, and are [discussed by the Real-ESRGAN developer here](https://github.com/xinntao/Real-ESRGAN#european_castle-model-zoo).

|Model|Description|
|---------|---------|
|[RealESRGAN_x4plus](https://github.com/xinntao/Real-ESRGAN/releases/download/v0.1.0/RealESRGAN_x4plus.pth)|X4 model for general images. This is the default model used in vqgan_clip_generator.esrgan|
|[RealESRGAN_x4plus_anime_6B](https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.2.4/RealESRGAN_x4plus_anime_6B.pth)|Optimized for anime images; 6 RRDB blocks (slightly smaller network)|
|[RealESRGAN_x2plus](https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.1/RealESRGAN_x2plus.pth)|X2 model for general images|
|[RealESRNet_x4plus](https://github.com/xinntao/Real-ESRGAN/releases/download/v0.1.1/RealESRNet_x4plus.pth)|X4 model with MSE loss (over-smooth effects)|
|[official ESRGAN_x4](https://github.com/xinntao/Real-ESRGAN/releases/download/v0.1.1/ESRGAN_SRx4_DF2KOST_official-ff704c30.pth)|official ESRGAN model (X4)|

Many additional models can be found at the [Upscale Wiki Model Database](https://upscale.wiki/wiki/Model_Database). None of these have been tested with this package, but are described as being compatible, and as having been trained for specific image restoration / processing tasks.

## Using Real-ESRGAN
A wrapper for Real-ESRGAN is provided that is an adaptation of the command-line script created by the authors. The esrgan.inference_realesrgan() method provided here converts the original method into a python-callable function, and provides dynamic model file downloading and caching.

A few key arguments are discussed below. See [the original Real-ESRGAN repository](https://github.com/xinntao/Real-ESRGAN) for more discussion.

|Argument|Default|Discussion|
|--------|-------|----------|
|input|'./video_frames'|Path to the image file or folder to upscale. If a folder is passed, all images in the folder will be processed.|
|model_filename|'RealESRGAN_x4plus.pth'|The Real-ESRGAN compatible model file to be used. If not cached, it will be downloaded.|
|model_url|f'https://github.com/xinntao/Real-ESRGAN/releases/download/v0.1.0/RealESRGAN_x4plus.pth'| The URL to download the model from if it is not cached.|
|output_images_path|'./upscaled_video_frames'| Location to save output images.|
|**purge_existing_files**|False|If true, ***all files in the output_images_path folder will be deleted*** before new images are created. This is useful when processing exported frames from a restyle video. Do not use this on your ./outputs folder!|
|netscale|4|Upsample scale factor of the network.|
|outscale|4|The final upsampling scale of the image. It's not clear to me the difference between these two arguments.|
|face_enhance|False|Use GFPGAN to enhance faces, while also upsampling using the model selected in model_filename/model_url.|

[Examples](https://github.com/rkhamilton/vqgan-clip-generator/tree/main/examples) are provided which include an optional upscaling step using the default upscaler. 

A simple example is shown below for upscaling a single image. The output file will be saved to the output_images folder with the same filename as the input. If you attempt to save to the same folder as the original image, the filename will be appended with '_upscaled'. If the input argument is a folder name, all images in the input folder will be upscaled and saved to output.
```python
esrgan.inference_realesrgan(input='my_original_image.png',
                output_images_path='output',
                face_enhance=False)
```
Here is an example of using Real-ESRGAN to upscale a generated image.
```python
from vqgan_clip import generate, esrgan
from vqgan_clip.engine import VQGAN_CLIP_Config
import os

config = VQGAN_CLIP_Config()
config.output_image_size = [448,448]
upscale_image = True
text_prompts = 'A pastoral landscape painting by Rembrandt'

output_filename = os.path.join('output',text_prompts)
generate.single_image(eng_config = config,
        text_prompts = text_prompts,
        iterations = 100,
        save_every = 50,
        output_filename = output_filename)

# Upscale the video frames
if upscale_image:
        esrgan.inference_realesrgan(input=output_filename+'.png',
                output_images_path='output',
                face_enhance=True)
```