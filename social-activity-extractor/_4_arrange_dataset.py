# -*- coding: utf-8 -*-
import os
import shutil
import config
import re
import sys
import csv
import _pickle as cPickle
import numpy as np
import pandas as pd

from tqdm import tqdm

import torch
import torch.nn as nn
import torchvision.models as models
import torchvision.transforms as transforms
from torchvision.datasets.folder import pil_loader
from gensim.models.keyedvectors import FastTextKeyedVectors
from gensim.similarities.index import AnnoyIndexer

from util import process_text

CONFIG = config.Config

def copy_selected_post(target_folder):

	path_to_posts = {}
	data_path = os.path.join(CONFIG.DATA_PATH, target_folder)

	for directory in os.listdir(data_path):
		path_dir = os.path.join(data_path, directory)
		path_to_posts[directory] = []
		for file in os.listdir(path_dir):
			if file.endswith('UTC.txt'):
				path_to_posts[directory].append(file)

	print("Total # of locations: ", len(path_to_posts))

	data_path = os.path.join(CONFIG.DATA_PATH, target_folder)
	dataset_path = os.path.join(CONFIG.DATASET_PATH, target_folder)
	if not os.path.exists(dataset_path):
		os.mkdir(dataset_path)
	count = 0
	for directory, posts in path_to_posts.items():
		print(str(count), "th Location directory: ", directory)
		path_dir = os.path.join(data_path, directory)


		for file in os.listdir(path_dir):
			if file.endswith('location.txt'):
				os.remove(os.path.join(path_dir, file))
				continue
			if not file.endswith('.jpg') and not file.endswith('.txt') and not file.endswith('.json'):
				os.remove(os.path.join(path_dir, file))
				continue

		for post in tqdm(posts):
			post_name = post.replace(".txt", "")
			post_dic = {"img":[], "text":"", "json":""}
			for file in os.listdir(path_dir):
				if file.startswith(post_name):
					if file.endswith('.jpg'):
						post_dic['img'].append(file)
					elif file.endswith('.json'):
						post_dic['json'] = file
					elif file.endswith('.txt') and not file.endswith('location.txt'):
						post_dic['text'] = file
					else:
						pass

			if len(post_dic["img"]) > 0 and post_dic["text"] != "" and post_dic["json"] != "":

				with open(os.path.join(path_dir, post_dic["text"]), 'r', encoding='utf-8', newline='\n') as f:
					# print("file: ", text_file)
					data = f.read()
					line = process_text(data)
					if len(line) > 0:						
						path_to_location = os.path.join(dataset_path, directory)
						if not os.path.exists(path_to_location):
							os.mkdir(path_to_location)
						path_to_post = os.path.join(dataset_path, directory, post_name)
						if not os.path.exists(path_to_post):
							os.mkdir(path_to_post)
						shutil.move(os.path.join(path_dir, post_dic["json"]), os.path.join(path_to_post, "meta.json"))
						os.mkdir(os.path.join(path_to_post, "images"))
						for idx, img in enumerate(post_dic["img"]):
							img_name = "image_" + str(idx) + ".jpg"
							shutil.move(os.path.join(path_dir, img), os.path.join(path_to_post, "images", img_name))
						f_wr = open(os.path.join(path_to_post, "text.txt"), 'w', encoding='utf-8')
						f_wr.write(line + ' <EOS>\n')
						f_wr.close()
				f.close()
		shutil.rmtree(path_dir)
		count = count + 1

	print("Copy completed")


class Identity(nn.Module):
	def __init__(self):
		super(Identity, self).__init__()
		
	def forward(self, x):
		return x

def embedding_images(target_dataset):
	device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
	print("Loading embedding model...")
	embedding_model = models.resnet50(pretrained=True)
	embedding_model.fc = Identity()
	embedding_model.eval()
	embedding_model.to(device)
	print("Loading embedding model completed")
	# pad_value = np.finfo(np.float32).eps
	pad_value = 1.
	dataset_path = os.path.join(CONFIG.DATASET_PATH, target_dataset)
	img_transform = transforms.Compose([
					transforms.Resize(256),
					transforms.CenterCrop(224),
					transforms.ToTensor(),
					transforms.Normalize(mean=[0.485, 0.456, 0.406],
									 std=[0.229, 0.224, 0.225])
				])	
	for loc_id in tqdm(os.listdir(dataset_path)):
		path_dir = os.path.join(dataset_path, loc_id)
		for post in tqdm(os.listdir(path_dir), leave=False):
			pickle_path = os.path.join(path_dir, post, "images.p")
			image_dir = os.path.join(path_dir, post, "images")
			images = []
			for image in os.listdir(image_dir):
				image_path = os.path.join(image_dir, image)
				try:
					images.append(img_transform(pil_loader(image_path)))
				except OSError as e:
					print(e)
					print(image_path)
			image_data = torch.stack(images).detach().numpy()
			with open(pickle_path, 'wb') as f:
				cPickle.dump(image_data, f)
			f.close()
			del image_data
			# image_data = torch.stack(images).to(device)
			# embedded_image = embedding_model(image_data).detach().cpu().numpy()
			# if len(embedded_image) < CONFIG.MAX_SEQUENCE_LEN:
			# 	# pad sentence with 0 if sentence length is shorter than `max_sentence_len`
			# 	vector_array = np.lib.pad(embedded_image,
			# 							((0, CONFIG.MAX_SEQUENCE_LEN - len(embedded_image)), (0,0)),
			# 							"constant",
			# 							constant_values=(pad_value))
			# else:
			# 	vector_array = embedded_image
			# with open(pickle_path, 'wb') as f:
			# 	cPickle.dump(vector_array, f)
			# f.close()
			# del image_data, embedded_image, vector_array

def embedding_text(target_dataset):
	print("Loading embedding model...")
	model_name = 'FASTTEXT_' + target_dataset + '.model'
	embedding_model = FastTextKeyedVectors.load(os.path.join(CONFIG.EMBEDDING_PATH, model_name))
	print("Loading embedding model completed")
	dataset_path = os.path.join(CONFIG.DATASET_PATH, target_dataset)
	for loc_id in tqdm(os.listdir(dataset_path)):
		path_dir = os.path.join(dataset_path, loc_id)
		for post in tqdm(os.listdir(path_dir), leave=False):
			pickle_path = os.path.join(path_dir, post, "text.p")
			with open(os.path.join(path_dir, post, "text.txt"), 'r', encoding='utf-8', newline='\n') as f:
				text_data = f.read()
				word_list = text_data.split()
				vector_list = []
				if len(word_list) > CONFIG.MAX_SENTENCE_LEN:
					# truncate sentence if sentence length is longer than `max_sentence_len`
					word_list = word_list[:CONFIG.MAX_SENTENCE_LEN]
					word_list[-1] = '<EOS>'
				else:
					word_list = word_list + ['<PAD>'] * (CONFIG.MAX_SENTENCE_LEN - len(word_list))
				for word in word_list:
					vector = embedding_model.get_vector(word)
					vector_list.append(vector)
				vector_array = np.array(vector_list, dtype=np.float32)
			f.close()
			with open(pickle_path, 'wb') as f:
				cPickle.dump(vector_array, f, protocol=-1)
			f.close()
			del text_data, word_list, vector_array

def process_dataset(target_dataset):
	device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
	print("Loading embedding model...")
	embedding_model = models.resnet50(pretrained=True)
	embedding_model.fc = Identity()
	embedding_model.eval()
	embedding_model.to(device)
	print("Loading embedding model completed")
	# pad_value = np.finfo(np.float32).eps
	pad_value = 1.
	img_transform = transforms.Compose([
					transforms.Resize(256),
					transforms.CenterCrop(224),
					transforms.ToTensor(),
					transforms.Normalize(mean=[0.485, 0.456, 0.406],
									 std=[0.229, 0.224, 0.225])
				])	
	dataset_path = os.path.join(CONFIG.DATASET_PATH, target_dataset)
	f_csv = open(os.path.join(dataset_path, 'posts.csv'), 'w', encoding='utf-8')
	f_corpus = open(os.path.join(dataset_path, 'corpus.txt'), 'w', encoding='utf-8')
	wr = csv.writer(f_csv)
	target_path = os.path.join('Y:', 'placeness')
	df_data = pd.read_csv(os.path.join(target_path, 'posts.csv'), encoding='utf-8')
	pbar = tqdm(total=df_data.shape[0])
	for index, in_row in df_data.iterrows():
		pbar.update(1)
		if pd.isna(in_row.iloc[2]):
			continue
		sentence = process_text(in_row.iloc[2])
		images = []
		for image in in_row.iloc[7:]:
			if not pd.isna(image):
				image_path = os.path.join(target_path, 'original', image)
				try:
					images.append(img_transform(pil_loader(image_path)))
				except OSError as e:
					print(e)
					print(image_path)
		if len(sentence) > 0 and len(images) > 0:
			with torch.no_grad():
				image_data = torch.stack(images).to(device)
			embedded_image = embedding_model(image_data).detach().cpu().numpy()
			if len(embedded_image) < CONFIG.MAX_SEQUENCE_LEN:
				# pad sentence with 0 if sentence length is shorter than `max_sentence_len`
				vector_array = np.lib.pad(embedded_image,
										((0, CONFIG.MAX_SEQUENCE_LEN - len(embedded_image)), (0,0)),
										"constant",
										constant_values=(pad_value))
			else:
				vector_array = embedded_image
			sentence = sentence + ' <EOS>'
			out_row = []
			out_row.append(in_row.iloc[1])
			out_row.append(sentence)
			wr.writerow(out_row)
			f_corpus.write(sentence + '\n')
			with open(os.path.join(dataset_path, 'resnet50', in_row.iloc[1]+'.p'), 'wb') as f:
				cPickle.dump(vector_array, f)
			f.close()
			del image_data, embedded_image, vector_array
	pbar.close()

def test():
	dataset_path = os.path.join(CONFIG.DATASET_PATH, "instagram_full", "images", "Bk4yiNHna1R.p")
	with open(dataset_path, "rb") as f:
		data = cPickle.load(f)
		print(data[0])
		print(data.shape)
	#df_data = pd.read_csv(os.path.join(CONFIG.DATASET_PATH, target_dataset, 'posts.csv'), header=None, encoding='utf-8')
	#print(df_data)

def run(option): 
	if option == 0:
		copy_selected_post(target_folder=sys.argv[2])
	elif option == 1:
		embedding_images(target_dataset=sys.argv[2])
	elif option == 2:
		embedding_text(target_dataset=sys.argv[2])
	elif option == 3:
		process_dataset(target_dataset=sys.argv[2])
	elif option == 4:
		test()
	else:
		print("This option does not exist!\n")


if __name__ == '__main__':
	run(int(sys.argv[1]))