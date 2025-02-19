import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.nn.init
from torch.autograd import Variable

import math
import numpy as np

from model.component import SiLU, Maxout, PTanh

class MultimodalEncoder(nn.Module):
	def __init__(self, text_encoder, imgseq_encoder, latent_size):
		super(MultimodalEncoder, self).__init__()
		self.text_encoder = text_encoder
		self.imgseq_encoder = imgseq_encoder
		self.latent_size = latent_size
		self.multimodal_encoder = nn.Sequential(
			nn.Linear(latent_size*2, int(latent_size*2/3)),
			nn.SELU(),
			nn.Linear(int(latent_size*2/3), latent_size))
	def __call__(self, text, imgseq):
		text_h = self.text_encoder(text)
		imgseq_h = self.imgseq_encoder(imgseq)
		h = self.multimodal_encoder(torch.cat((text_h, imgseq_h), dim=-1))
		return h

class MultimodalDecoder(nn.Module):
	def __init__(self, text_decoder, imgseq_decoder, latent_size, sequence_len):
		super(MultimodalDecoder, self).__init__()
		self.text_decoder = text_decoder
		self.imgseq_decoder =imgseq_decoder
		self.sequence_len = sequence_len
		self.latent_size = latent_size
		self.multimodal_decoder = nn.Sequential(
			nn.Linear(latent_size, int(latent_size*2/3)),
			nn.SELU(),
			nn.Linear(int(latent_size*2/3), latent_size*2),
			nn.Tanh())

	def __call__(self, h):
		decode_h = torch.split(self.multimodal_decoder(h), self.latent_size, dim=-1)
		text_hat = self.text_decoder(decode_h[0])
		imgseq_hat = self.imgseq_decoder(decode_h[1])
		return text_hat, imgseq_hat

class MultimodalAutoEncoder(nn.Module):
	def __init__(self, encoder, decoder):
		super(MultimodalAutoEncoder, self).__init__()
		self.encoder = encoder
		self.decoder = decoder

	def forward(self, text, imgseq):
		h = self.encoder(text, imgseq)
		text_hat, imgseq_hat = self.decoder(h)
		return text_hat, imgseq_hat