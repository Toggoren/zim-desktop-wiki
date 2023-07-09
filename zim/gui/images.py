# Copyright 2008-2021 Jaap Karssenberg <jaap.karssenberg@gmail.com>

"""This module contains the image processing functions
"""

import logging

from gi.repository import GLib
from gi.repository import GdkPixbuf

logger = logging.getLogger('zim.gui.images')


def image_file_get_dimensions(file_path):
	"""
	Replacement for GdkPixbuf.Pixbuf.get_file_info
	@return (width, height) in pixels
		or None if file does not exist or failed to load
	"""

	# Let GTK try reading the file
	_, width, height = GdkPixbuf.Pixbuf.get_file_info(file_path)
	if width > 0 and height > 0:
		return (width, height)

	# Fallback to Pillow
	try:
		from PIL import Image # load Pillow only if necessary
		with Image.open(file_path) as img_pil:
			return (img_pil.width, img_pil.height)
	except:
		raise AssertionError('Could not get size for: %s' % file_path)


def image_file_load_pixels(file, width_override=-1, height_override=-1):
	"""
	Replacement for GdkPixbuf.Pixbuf.new_from_file_at_size(file.path, w, h)
	When file does not exist or fails to load, this throws exceptions.
	"""

	if not file.exists():
		# if the file does not exist, no need to make the effort of trying to read it
		raise FileNotFoundError(file.path)

	b_size_override = width_override > 0 or height_override > 0
	if b_size_override and (width_override <= 0 or height_override <= 0):
		w, h = image_file_get_dimensions(file.path) # can raise
		if height_override <= 0:
			height_override = int(h * width_override / w)
		else:
			width_override = int(w * height_override / h)

	# Let GTK try reading the file
	try:
		pixbuf = GdkPixbuf.Pixbuf.new_from_file(file.path)

		if b_size_override:
			pixbuf = pixbuf.scale_simple(width_override, height_override, GdkPixbuf.InterpType.BILINEAR)
				# do not use new_from_file_at_size() here due to bug in Gtk for GIF images, see issue #1563

		pixbuf = GdkPixbuf.Pixbuf.apply_embedded_orientation(pixbuf)

	except:
		logger.debug('GTK failed to read image, using Pillow fallback: %s', file.path)

		from PIL import Image # load Pillow only if necessary

		with Image.open(file.path) as img_pil:

			# resize if a specific size was requested
			if b_size_override:
				logger.debug('PIL resizing %s %s', width_override, height_override)
				img_pil = img_pil.resize((width_override, height_override))

			# check if there is an alpha channel
			if img_pil.mode == 'RGB':
				has_alpha = False
			elif img_pil.mode == 'RGBA':
				has_alpha = True
			else:
				raise ValueError('Pixel format {fmt} can not be converted to Pixbuf for image {p}'.format(
					fmt = img_pil.mode, p = file.path,
				))

			# convert to GTK pixbuf
			data_gtk = GLib.Bytes.new_take(img_pil.tobytes())

			pixbuf = GdkPixbuf.Pixbuf.new_from_bytes(
				data = data_gtk,
				colorspace = GdkPixbuf.Colorspace.RGB,
				has_alpha = has_alpha,
				# GTK docs: "Currently only RGB images with 8 bits per sample are supported"
				# https://developer.gnome.org/gdk-pixbuf/stable/gdk-pixbuf-Image-Data-in-Memory.html#gdk-pixbuf-new-from-bytes
				bits_per_sample = 8,
				width = img_pil.width,
				height = img_pil.height,
				rowstride = img_pil.width * (4 if has_alpha else 3),
			)

	return pixbuf
