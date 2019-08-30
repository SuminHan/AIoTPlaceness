import argparse
import config
import requests
import json
import pickle
import datetime
import os
import math
import numpy as np
import pandas as pd
from sumeval.metrics.rouge import RougeCalculator
from sumeval.metrics.bleu import BLEUCalculator
from gensim.models.keyedvectors import FastTextKeyedVectors
from gensim.similarities.index import AnnoyIndexer
from hyperdash import Experiment
from tqdm import tqdm


import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import DataLoader
from torch.autograd import Variable
from torch.optim.adam import Adam
from torch.optim.lr_scheduler import StepLR, CyclicLR
from model import util
from model import text_model
from model.util import load_text_data
from model.component import AdamW



CONFIG = config.Config

def slacknoti(contentstr):
	webhook_url = "https://hooks.slack.com/services/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"
	payload = {"text": contentstr}
	requests.post(webhook_url, data=json.dumps(payload), headers={'Content-Type': 'application/json'})

def main():
	parser = argparse.ArgumentParser(description='text convolution-deconvolution auto-encoder model')
	# learning
	parser.add_argument('-lr', type=float, default=3e-04, help='initial learning rate')
	parser.add_argument('-weight_decay', type=float, default=3e-05, help='initial weight decay')
	parser.add_argument('-epochs', type=int, default=80, help='number of epochs for train')
	parser.add_argument('-batch_size', type=int, default=16, help='batch size for training')
	parser.add_argument('-lr_decay_interval', type=int, default=10,
						help='how many epochs to wait before decrease learning rate')
	parser.add_argument('-log_interval', type=int, default=100,
						help='how many steps to wait before logging training status')
	parser.add_argument('-test_interval', type=int, default=1,
						help='how many epochs to wait before testing')
	parser.add_argument('-save_interval', type=int, default=1,
						help='how many epochs to wait before saving')
	# data
	parser.add_argument('-target_dataset', type=str, default=None, help='folder name of target dataset')
	parser.add_argument('-shuffle', default=True, help='shuffle data every epoch')
	parser.add_argument('-split_rate', type=float, default=0.9, help='split rate between train and validation')
	# model
	parser.add_argument('-latent_size', type=int, default=900, help='size of latent variable')
	parser.add_argument('-filter_size', type=int, default=300, help='filter size of convolution')
	parser.add_argument('-filter_shape', type=int, default=5,
						help='filter shape to use for convolution')
	parser.add_argument('-num_layer', type=int, default=4, help='layer number')

	# train
	parser.add_argument('-noti', action='store_true', default=False, help='whether using gpu server')
	# option
	parser.add_argument('-resume', type=str, default=None, help='filename of checkpoint to resume ')

	args = parser.parse_args()

	if args.noti:
		slacknoti("underkoo start using")
	train_reconstruction(args)
	if args.noti:
		slacknoti("underkoo end using")


device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
def train_reconstruction(args):
	print("Loading embedding model...")
	model_name = 'FASTTEXT_' + args.target_dataset + '.model'
	embedding_model = FastTextKeyedVectors.load(os.path.join(CONFIG.EMBEDDING_PATH, model_name))
	embedding_dim = embedding_model.vector_size	
	args.embedding_dim = embedding_dim
	print("Building index...")
	indexer = AnnoyIndexer(embedding_model, 10)
	print("Loading embedding model completed")
	print("Loading dataset...")
	train_dataset, val_dataset = load_text_data(args, CONFIG, embedding_model)
	print("Loading dataset completed")
	train_loader, val_loader = DataLoader(train_dataset, batch_size=args.batch_size, shuffle=args.shuffle),\
								  DataLoader(val_dataset, batch_size=args.batch_size, shuffle=False)

	# t1 = max_sentence_len + 2 * (args.filter_shape - 1)
	t1 = CONFIG.MAX_SENTENCE_LEN
	t2 = int(math.floor((t1 - args.filter_shape) / 2) + 1) # "2" means stride size
	t3 = int(math.floor((t2 - args.filter_shape) / 2) + 1)
	args.t3 = t3	

	text_encoder = text_model.ConvolutionEncoder(embedding_dim, t3, args.filter_size, args.filter_shape, args.latent_size)
	text_decoder = text_model.DeconvolutionDecoder(embedding_dim, t3, args.filter_size, args.filter_shape, args.latent_size)
	if args.resume:
		print("Restart from checkpoint")
		checkpoint = torch.load(os.path.join(CONFIG.CHECKPOINT_PATH, args.resume), map_location=lambda storage, loc: storage)
		best_loss = checkpoint['best_loss']
		start_epoch = checkpoint['epoch']
		text_encoder.load_state_dict(checkpoint['text_encoder'])
		text_decoder.load_state_dict(checkpoint['text_decoder'])
	else:		
		print("Start from initial")
		best_loss = 999999.
		start_epoch = 0
	
	text_autoencoder = text_model.TextAutoencoder(text_encoder, text_decoder)
	criterion = nn.MSELoss().to(device)
	text_autoencoder.to(device)

	optimizer = AdamW(text_autoencoder.parameters(), lr=args.lr, weight_decay=args.weight_decay, amsgrad=True)

	if args.resume:
		optimizer.load_state_dict(checkpoint['optimizer'])


	exp = Experiment("Text autoencoder")
	try:
		avg_loss = []
		rouge_1 = []
		rouge_2 = []

		text_autoencoder.train() 

		for epoch in range(start_epoch, args.epochs):
			print("Epoch: {}".format(epoch))
			for steps, batch in enumerate(train_loader):
				torch.cuda.empty_cache()
				feature = Variable(batch).to(device)
				optimizer.zero_grad()
				feature_hat = text_autoencoder(feature)
				loss = criterion(feature_hat, feature)
				loss.backward()
				optimizer.step()

				if steps % args.log_interval == 0:
					print("Epoch: {} at {}".format(epoch, str(datetime.datetime.now())))
					print("Steps: {}".format(steps))
					print("Loss: {}".format(loss.detach().item()))
					exp.metric("Loss", loss.detach().item())
					print("Test!!")
					input_data = feature[0]
					single_data = feature_hat[0]
					input_sentence = util.transform_vec2sentence(input_data.detach().cpu().numpy(), embedding_model, indexer)
					predict_sentence = util.transform_vec2sentence(single_data.detach().cpu().numpy(), embedding_model, indexer)
					print("Input Sentence:")
					print(input_sentence)
					print("Output Sentence:")
					print(predict_sentence)
					del input_data, single_data
				del feature, feature_hat, loss
			
			_avg_loss, _rouge_1, _rouge_2 = eval_reconstruction(text_autoencoder, embedding_model, indexer, criterion, val_loader, args)
			avg_loss.append(_avg_loss)
			rouge_1.append(_rouge_1)
			rouge_2.append(_rouge_2)

			if best_loss > _avg_loss:
				best_loss = _avg_loss
				util.save_models({
					'epoch': epoch + 1,
					'text_encoder': text_encoder.state_dict(),
					'text_decoder': text_decoder.state_dict(),
					'best_loss': best_loss,
					'optimizer' : optimizer.state_dict(),
				}, CONFIG.CHECKPOINT_PATH, "text_autoencoder")

		# finalization
		table = []
		table.append(avg_loss)
		table.append(rouge_1)
		table.append(rouge_2)
		df = pd.DataFrame(table)
		df = df.transpose()
		df.columns = ['avg_loss', 'rouge_1', 'rouge_2']
		df.to_csv(os.path.join(CONFIG.CSV_PATH, 'Evaluation_result.csv'), encoding='utf-8-sig')

		print("Finish!!!")

	finally:
		exp.end()

def eval_reconstruction(autoencoder, embedding_model, indexer, criterion, data_iter, args):
	print("=================Eval======================")
	autoencoder.eval()
	step = 0
	avg_loss = 0.
	rouge_1 = 0.
	rouge_2 = 0.
	for batch in tqdm(data_iter):
		torch.cuda.empty_cache()
		with torch.no_grad():
			feature = Variable(batch).to(device)
		feature_hat = autoencoder(feature)
		original_sentences = [util.transform_vec2sentence(sentence, embedding_model, indexer) for sentence in feature.detach().cpu().numpy()]		
		predict_sentences = [util.transform_vec2sentence(sentence, embedding_model, indexer) for sentence in feature_hat.detach().cpu().numpy()]	
		r1, r2 = calc_rouge(original_sentences, predict_sentences)		
		rouge_1 += r1 / len(batch)
		rouge_2 += r2 / len(batch)
		loss = criterion(feature_hat, feature)	
		avg_loss += loss.detach().item()
		step = step + 1
		del feature, feature_hat, loss
	avg_loss = avg_loss / step
	rouge_1 = rouge_1 / step
	rouge_2 = rouge_2 / step
	print("Evaluation - loss: {}  Rouge1: {}    Rouge2: {}".format(avg_loss, rouge_1, rouge_2))
	print("===============================================================")
	autoencoder.train()

	return avg_loss, rouge_1, rouge_2

def calc_rouge(original_sentences, predict_sentences):
	rouge_1 = 0.0
	rouge_2 = 0.0
	for original, predict in zip(original_sentences, predict_sentences):
		# Remove padding
		original, predict = original.replace("<PAD>", "").strip(), predict.replace("<PAD>", "").strip()
		rouge = RougeCalculator(stopwords=True, lang="en")
		r1 = rouge.rouge_1(summary=predict, references=original)
		r2 = rouge.rouge_2(summary=predict, references=original)
		rouge_1 += r1
		rouge_2 += r2
	return rouge_1, rouge_2


if __name__ == '__main__':
	main()