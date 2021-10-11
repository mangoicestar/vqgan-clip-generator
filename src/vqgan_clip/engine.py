# This contains the original math to generate an image from VQGAN+CLIP. I don't fully understand what it's doing and don't expect to change it.
from . import _functional as VF

import torch
from torch import optim
from torch.nn import functional as F
from torchvision.transforms import functional as TF
from torch.cuda import get_device_properties
torch.backends.cudnn.benchmark = False		# NR: True is a bit faster, but can lead to OOM. False is more deterministic.
#torch.use_deterministic_algorithms(True)	# NR: grid_sampler_2d_backward_cuda does not have a deterministic implementation
from torch_optimizer import DiffGrad, AdamP, RAdam
import clip
from PIL import ImageFile, Image, PngImagePlugin
ImageFile.LOAD_TRUNCATED_IMAGES = True
import imageio
from tqdm import tqdm

from urllib.request import urlopen

import numpy as np

import os

__all__ = ["VQGAN_CLIP_Config", "Engine"]


class VQGAN_CLIP_Config:
    """A set of attributes used to customize the execution of VQGAN+CLIP

    Instantiate VQGAN_CLIP_Config then customize attributes as described below.
    * output_image_size (list of int) : x/y dimensions of the output image in pixels. This will be adjusted slightly based on the GAN model used. Default = [256,256]
    * init_image (str) : A seed image that can be used to start the training. Without an initial image, random noise will be used. Default = None  
    * init_noise (str) : Seed an image with noise. Options None, \'pixels\' or \'gradient\'  Default = None 
    * init_weight (float) : Used to keep some similarity to the initial image. Not tested here. Default = 0.0
    * self.seed = None # Integer to use as seed for the random number generaor. If None, a random value will be chosen.  
    * self.clip_model = \'ViT-B/32\' # options \'ViT-B/32\', \'ViT-B/16)\', default to \'ViT-B/32\'  
    * self.vqgan_config = f\'models/vqgan_imagenet_f16_16384.yaml\' # path to model yaml file  
    * self.vqgan_checkpoint = f\'models/vqgan_imagenet_f16_16384.ckpt\' # path to model checkpoint file  
    * self.learning_rate = 0.1  
    * self.cut_method = \'latest\' # choices=[\'original\',\'updated\',\'nrupdated\',\'updatedpooling\',\'latest\'] default=\'latest\'  
    * self.num_cuts = 32  
    * self.cut_power = 1.0  
    * self.cudnn_determinism = False # if true, use algorithms that have reproducible, deterministic output. Performance will be lower.  
    * self.optimiser = \'Adam\' # choices=[\'Adam\',\'AdamW\',\'Adagrad\',\'Adamax\',\'DiffGrad\',\'AdamP\',\'RAdam\',\'RMSprop\'], default=\'Adam\'  
    * self.augments = [[\'Af\', \'Pe\', \'Ji\', \'Er\']] # I have no idea what this does. choices=[\'Ji\',\'Sh\',\'Gn\',\'Pe\',\'Ro\',\'Af\',\'Et\',\'Ts\',\'Cr\',\'Er\',\'Re\']  
    * self.cuda_device = \'cuda:0\' # select your GPU. Default to the first gpu, device 0  
    """
    def __init__(self):
        self.output_image_size = [256,256] # x/y dimensions of the output image in pixels. This will be adjusted slightly based on the GAN model used.
        self.init_image = None # a seed image that can be used to start the training. Without an initial image, random noise will be used.
        self.init_noise = None # seed an image with noise. Options None, 'pixels' or 'gradient'
        self.init_weight = 0.0 # used to keep some similarity to the initial image. Not tested here.
        self.seed = None # Integer to use as seed for the random number generaor. If None, a random value will be chosen.
        self.clip_model = 'ViT-B/32' # options 'ViT-B/32', 'ViT-B/16)', default to 'ViT-B/32'
        self.vqgan_config = f'models/vqgan_imagenet_f16_16384.yaml' # path to model yaml file
        self.vqgan_checkpoint = f'models/vqgan_imagenet_f16_16384.ckpt' # path to model checkpoint file
        self.learning_rate = 0.1
        self.cut_method = 'latest' # choices=['original','updated','nrupdated','updatedpooling','latest'] default='latest'
        self.num_cuts = 32
        self.cut_power = 1.0
        self.cudnn_determinism = False # if true, use algorithms that have reproducible, deterministic output. Performance will be lower.
        self.optimiser = 'Adam' # choices=['Adam','AdamW','Adagrad','Adamax','DiffGrad','AdamP','RAdam','RMSprop'], default='Adam'
        self.augments = [['Af', 'Pe', 'Ji', 'Er']] # I have no idea what this does. choices=['Ji','Sh','Gn','Pe','Ro','Af','Et','Ts','Cr','Er','Re']
        self.cuda_device = 'cuda:0' # select your GPU. Default to the first gpu, device 0

class Engine:
    def __init__(self, config=VQGAN_CLIP_Config()):
        # self._optimiser = optim.Adam([self._z], lr=0.1)
        self.apply_configuration(config)

        self._gumbel = False

        self.replace_grad = VF.ReplaceGrad.apply
        self.clamp_with_grad = VF.ClampWithGrad.apply

        self.seed = torch.seed()

        self.clear_all_prompts()

    def apply_configuration(self,config):
        """Apply an instance of VQGAN_CLIP_Config to this Engine instance

        Args:
            config (VQGAN_CLIP_Config): An instance of VQGAN_CLIP_Config that has been customized for this run.
        """
        self.conf = config

        # default_image_size = 512  # >8GB VRAM
        # if not torch.cuda.is_available():
        #     default_image_size = 256  # no GPU found
        # elif get_device_properties(0).total_memory <= 2 ** 33:  # 2 ** 33 = 8,589,934,592 bytes = 8 GB
        #     default_image_size = 318  # <8GB VRAM


    def set_seed(self, seed):
        """Set the seed for the random number generator used by VQGAN-CLIP

        Args:
            seed (int): Integer seed for the random number generator. The code is still non-deterministic unless cudnn_determinism = False is used in the configuration.
        """
        self.seed = seed
        torch.manual_seed(seed)

    # Set the optimiser
    def configure_optimizer(self):
        """Configure the optimization algorithm selected in self.conf.optimiser. This must be done immediately before training with train()
        """
        opt_name = self.conf.optimiser
        opt_lr = self.conf.learning_rate
        if opt_name == "Adam":
            self._optimizer = optim.Adam([self._z], lr=opt_lr)	# LR=0.1 (Default)
        elif opt_name == "AdamW":
            self._optimizer = optim.AdamW([self._z], lr=opt_lr)	
        elif opt_name == "Adagrad":
            self._optimizer = optim.Adagrad([self._z], lr=opt_lr)	
        elif opt_name == "Adamax":
            self._optimizer = optim.Adamax([self._z], lr=opt_lr)	
        elif opt_name == "DiffGrad":
            self._optimizer = DiffGrad([self._z], lr=opt_lr, eps=1e-9, weight_decay=1e-9) # NR: Playing for reasons
        elif opt_name == "AdamP":
            self._optimizer = AdamP([self._z], lr=opt_lr)		    
        elif opt_name == "RAdam":
            self._optimizer = RAdam([self._z], lr=opt_lr)		    
        elif opt_name == "RMSprop":
            self._optimizer = optim.RMSprop([self._z], lr=opt_lr)
        else:
            print("Unknown optimiser.")
            self._optimizer = optim.Adam([self._z], lr=opt_lr)

    def train(self, iteration_number):
        """Executes training of the already-initialized VQGAN-CLIP model to generate an image. After a user-desired number of calls to train(), use save_current_output() to save the generated image.

        Args:
            iteration_number (int): Current iteration number, used only to adjust the weight of the inital image if init_weight is used.

        Returns:
            lossAll (tensor): A list of losses from the training process
        """
        self._optimizer.zero_grad(set_to_none=True)
        lossAll = self.ascend_txt(iteration_number)
        
        loss = sum(lossAll)
        loss.backward()
        self._optimizer.step()
        
        #with torch.no_grad():
        with torch.inference_mode():
            self._z.copy_(self._z.maximum(self.z_min).minimum(self.z_max))
        
        return lossAll

    def save_current_output(self, save_filename):
        """Save the current output from the image generator as a PNG file to location save_filename

        Args:
            save_filename (str): string containing the path to save the generated image. e.g. 'output.png' or 'outputs/my_file.png'
        """
        # 
        with torch.inference_mode():
            # model_output = self.synth()
            info = PngImagePlugin.PngInfo()
            # If we have a text prompt for this image, add it as metadata
            # if self.story_phrase_current_prompt:
            #     info.add_text('comment', self.story_phrase_current_prompt[0])
            TF.to_pil_image(self.output_tensor[0].cpu()).save(save_filename, pnginfo=info)

    def ascend_txt(self,iteration_number):
        """Part of the process of training a GAN

        Args:
            iteration_number (int): Current iteration number, used only to adjust the weight of the inital image if init_weight is used.

        Returns:
            lossAll (tensor): Parameter describing the performance of the GAN training process
        """
        self.output_tensor = self.synth()
        encoded_image = self._perceptor.encode_image(VF.normalize(self._make_cutouts(self.output_tensor))).float()
        
        result = []

        if self.conf.init_weight:
            # result.append(F.mse_loss(self._z, z_orig) * args.init_weight / 2)
            result.append(F.mse_loss(self._z, torch.zeros_like(self._z_orig)) * ((1/torch.tensor(iteration_number*2 + 1))*self.conf.init_weight) / 2)

        for prompt in self.pMs:
            result.append(prompt(encoded_image))
        
        return result

    # Vector quantize
    def synth(self):
        if self._gumbel:
            z_q = VF.vector_quantize(self._z.movedim(1, 3), self._model.quantize.embed.weight).movedim(3, 1)
        else:
            z_q = VF.vector_quantize(self._z.movedim(1, 3), self._model.quantize.embedding.weight).movedim(3, 1)
        clamp_with_grad = VF.ClampWithGrad.apply
        return clamp_with_grad(self._model.decode(z_q).add(1).div(2), 0, 1)

    def initialize_VQGAN_CLIP(self):
        """Prior to using a VGQAN-CLIP engine instance, it must be initialized using this method.
        """
        if self.conf.cudnn_determinism:
            torch.backends.cudnn.deterministic = True

        self._device = torch.device(self.conf.cuda_device)
        self.load_model()
        jit = True if float(torch.__version__[:3]) < 1.8 else False
        self._perceptor = clip.load(self.conf.clip_model, jit=jit)[0].eval().requires_grad_(False).to(self._device)

        self.make_cutouts()    
        self.initialize_z()

    def encode_and_append_noise_prompt(self, prompt):
        """Encodes a weighted list of random number generator seeds using CLIP and appends those to the set of prompts being used by this model instance.
        
        example: encode_and_append_noise_prompt('1:1.0|2:0.2')

        Args:
            prompt (list of strings):   Takes as input a list of string prompts of the form 'number:weight'. The number must be an integer. 
                                        The number and weight are extracted and encoded by the CLIP perceptor, and stored by the Engine instance.
        """
        txt_seed, weight, _ = VF.split_prompt(prompt)
        seed = int(txt_seed)
        gen = torch.Generator().manual_seed(seed)
        embed = torch.empty([1, self._perceptor.visual.output_dim]).normal_(generator=gen)
        self.pMs.append(VF.Prompt(embed, weight).to(self._device))

    def initialize_z(self):
        # Gumbel or not?
        if self._gumbel:
            e_dim = 256
            n_toks = self._model.quantize.n_embed
            self.z_min = self._model.quantize.embed.weight.min(dim=0).values[None, :, None, None]
            self.z_max = self._model.quantize.embed.weight.max(dim=0).values[None, :, None, None]
        else:
            e_dim = self._model.quantize.e_dim
            n_toks = self._model.quantize.n_e
            self.z_min = self._model.quantize.embedding.weight.min(dim=0).values[None, :, None, None]
            self.z_max = self._model.quantize.embedding.weight.max(dim=0).values[None, :, None, None]

        if self.conf.init_image:
            if 'http' in self.conf.init_image:
                self.convert_image_to_init_image(Image.open(urlopen(self.conf.init_image)))
            else:
                self.convert_image_to_init_image(Image.open(self.conf.init_image))
        elif self.conf.init_noise == 'pixels':
            self.convert_image_to_init_image(VF.make_random_noise_image(self.conf.image_size[0], self.conf.image_size[1]))
        elif self.conf.init_noise == 'gradient':
            self.convert_image_to_init_image(VF.make_random_gradient_image(self.conf.image_size[0], self.conf.image_size[1]))
        else:
            # this is the default that happens if no initialization image options are specified
            f = 2**(self._model.decoder.num_resolutions - 1)
            toksX = self.conf.output_image_size[0] // f
            toksY = self.conf.output_image_size[1] // f
            one_hot = F.one_hot(torch.randint(n_toks, [toksY * toksX], device=self._device), n_toks).float()
            # self._z = one_hot @ self._model.quantize.embedding.weight
            if self._gumbel:
                self._z = one_hot @ self._model.quantize.embed.weight
            else:
                self._z = one_hot @ self._model.quantize.embedding.weight

            self._z = self._z.view([-1, toksY, toksX, e_dim]).permute(0, 3, 1, 2) 
            #self._z = torch.rand_like(self._z)*2						# NR: check
            self._z_orig = self._z.clone()
            self._z.requires_grad_(True)

    def calculate_output_image_size(self):
        f = 2**(self._model.decoder.num_resolutions - 1)
        output_image_size_X = (self.conf.output_image_size[0] // f) * f
        output_image_size_Y = (self.conf.output_image_size[1] // f) * f
        return output_image_size_X, output_image_size_Y

    def make_cutouts(self):
        # Cutout class options:
        # 'latest','original','updated', 'nrupdated', or 'updatedpooling'
        if self.conf.cut_method == 'latest':
            self._make_cutouts = VF.MakeCutouts(self._perceptor.visual.input_resolution, self.conf.num_cuts, self.conf.augments, cut_pow=self.conf.cut_power)
        elif self.conf.cut_method == 'original':
            self._make_cutouts = VF.MakeCutoutsOrig(self._perceptor.visual.input_resolution, self.conf.num_cuts, cut_pow=self.conf.cut_power)
        elif self.conf.cut_method == 'updated':
            self._make_cutouts = VF.MakeCutoutsUpdate(self._perceptor.visual.input_resolution, self.conf.num_cuts, cut_pow=self.conf.cut_power)
        elif self.conf.cut_method == 'nrupdated':
            self._make_cutouts = VF.MakeCutoutsNRUpdate(self._perceptor.visual.input_resolution, self.conf.num_cuts, self.conf.augments, cut_pow=self.conf.cut_power)
        else:
            self._make_cutouts = VF.MakeCutoutsPoolingUpdate(self._perceptor.visual.input_resolution, self.conf.num_cuts, cut_pow=self.conf.cut_power)

    def convert_image_to_init_image(self, pil_image):
        output_image_size_X, output_image_size_Y = self.calculate_output_image_size()
        pil_image = pil_image.convert('RGB')
        pil_image = pil_image.resize((output_image_size_X, output_image_size_Y), Image.LANCZOS)
        pil_tensor = TF.to_tensor(pil_image)
        self._z, *_ = self._model.encode(pil_tensor.to(self._device).unsqueeze(0) * 2 - 1)
        self._z_orig = self._z.clone()
        self._z.requires_grad_(True)

    def clear_all_prompts(self):
        """Clear all encoded prompts. You might use this during video generation to reset the prompts so that you can cause the video to steer in a new direction.
        """
        self.pMs = []

    def encode_and_append_image_prompt(self, prompt):
        """Encodes a list of image prompts using CLIP and appends those to the set of prompts being used by this model instance.
        
        example: encode_and_append_image_prompt('input\a_face.jpg:1.0|sample.png:0.2')

        Args:
            prompt (list of strings): Takes as input a list of string prompts of the form 'image_file_path:weight'. The file path and weight are extracted and encoded by the CLIP perceptor, and stored by the Engine instance.
        """
        # given an image prompt that is a filename followed by a weight e.g. 'prompt_image.png:0.5', load the image, encode it with CLIP, and append it to the list of prompts used for image generation
        output_image_size_X, output_image_size_Y = self.calculate_output_image_size()
        path, weight, stop = VF.split_prompt(prompt)
        output_image = Image.open(path)
        pil_image = output_image.convert('RGB')
        output_image = VF.resize_image(pil_image, (output_image_size_X, output_image_size_Y))
        batch = self._make_cutouts(TF.to_tensor(output_image).unsqueeze(0).to(self._device))
        embed = self._perceptor.encode_image(VF.normalize(batch)).float()
        self.pMs.append(VF.Prompt(embed, weight, stop).to(self._device))

    def encode_and_append_text_prompt(self, prompt):
        """Encodes a list of text prompts using CLIP and appends those to the set of prompts being used by this model instance.
        
        example: encode_and_append_text_prompt('A red sailboat:1.0|A cup of water:0.2')

        Args:
            prompt (list of strings): Takes as input a list of string prompts of the form 'text prompt:weight'. The prompt and weight are extracted and encoded by the CLIP perceptor, and stored by the Engine instance.
        """
        # given a text prompt like 'a field of red flowers:0.5' parse that into text and weights, encode it with CLIP, and add it to the encoded prompts used for image generation
        txt, weight, stop = VF.split_prompt(prompt)
        embed = self._perceptor.encode_text(clip.tokenize(txt).to(self._device)).float()
        self.pMs.append(VF.Prompt(embed, weight, stop).to(self._device))

    def load_model(self):
        # This step is slow, and does not need to be done each time an image is generated.
        self._model = VF.load_vqgan_model(self.conf.vqgan_config, self.conf.vqgan_checkpoint).to(self._device)

      
    def encode_and_append_prompts(self, prompt_number, text_prompts=[], image_prompts=[], noise_prompts=[]):
        """CLIP tokenize/encode the selected prompts from text, input images, and noise parameters
        Apply self.encode_and_append_text_prompt() to each of 
        text_prompts[prompt_number], 
        image_prompts[prompt_number], and 
        noise_prompts[prompt_number]

        If prompt_number is greater than the length of any of the lists above, it will roll over and encode from the beginning again.

        Args:
            prompt_number (int): The index of the prompt which should be encoded in series.  
            text_prompts (list of lists): List of lists text prompts that should all be applied in parallel
            image_prompts (list of lists): List of lists of text prompts that should all be applied in parallel
            noise_prompts (list of lists): List of lists of text prompts that should all be applied in parallel
            
        """
        if len(text_prompts) > 0:
            current_index = prompt_number % len(text_prompts)
            for prompt in text_prompts[current_index]:
                tqdm.write(f'Text prompt: {prompt_number}\n {prompt}')
                self.encode_and_append_text_prompt(prompt)
        
        # Split target images using the pipe character (weights are split later)
        if len(image_prompts) > 0:
            # if we had image prompts, encode them with CLIP
            current_index = prompt_number % len(image_prompts)
            for prompt in image_prompts[current_index]:
                tqdm.write(f'Image prompt: {prompt_number}\n {prompt}')
                self.encode_and_append_image_prompt(prompt)

        # Split noise prompts using the pipe character (weights are split later)
        if len(noise_prompts) > 0:
            current_index = prompt_number % len(noise_prompts)
            for prompt in noise_prompts[current_index]:
                tqdm.write(f'Noise prompt: {prompt_number}\n {prompt}')
                self.encode_and_append_noise_prompt(prompt)
