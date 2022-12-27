import os, os.path, subprocess, sys, tempfile
import shutil, struct

from pyparsedvd import load_vts_pgci
import cueparser
# noinspection PyPackageRequirements
import ffmpeg
from glob import glob
from pprint import pprint

def read_video_ifo(fn):
	with open(fn, 'rb') as fp:
		# noinspection PyTypeChecker
		return load_vts_pgci(fp)

def read_audio_ifo(fn):
	tracktimes = []
	with open(fn, 'rb') as fp:
		fp.seek(0xCC, 0)
		offset, = struct.unpack('>I', fp.read(4))
		fp.seek(2048 * offset, 0)
		numTitles, = struct.unpack('>H', fp.read(2))
		fp.read(6)
		titleOffsets = []
		for i in range(numTitles):
			fp.read(4)
			titleOffset, = struct.unpack('>I', fp.read(4))
			titleOffsets.append(titleOffset)
		for j, titleOffset in enumerate(titleOffsets):
			fp.seek(2048 * offset + titleOffset + 3, 0)
			numTracks = fp.read(1)[0]
			fp.read(10)
			sectorOffset, = struct.unpack('>H', fp.read(2))
			for i in range(numTracks):
				fp.seek(2048 * offset + titleOffset + 0x10 + 20 * i, 0)
				fp.read(4)
				trackNum = fp.read(1)[0]
				fp.read(1)
				firstPts, ptsLen = struct.unpack('>II', fp.read(8))
				fp.seek(2048 * offset + titleOffset + 0x10 + 20 * numTracks + i * 12 + 4, 0)
				firstSector, lastSector = struct.unpack('>II', fp.read(8))
				tracktimes.append((firstPts / 90000, ptsLen / 90000, firstSector, lastSector))
			break
	return tracktimes

#pprint(read_audio_ifo('test.ifo'))
#sys.exit(0)

def read_cue(fn):
	with open(fn, 'rb') as fp:
		cuesheet = cueparser.CueSheet()
		cuesheet.setOutputFormat('%performer% - %title%\n%file%\n%tracks%', '%performer% - %title%')
		cuesheet.setData(fp.read().decode('utf8'))
		cuesheet.parse()
		return cuesheet

codec_priority = ['mlp', 'ac3', 'dts', 'pcm_dvd']

def process_video_dvd(ipath, output):
	if len(glob('%s/*.VOB' % ipath)) == 0:
		if os.path.exists(ipath + '/VIDEO_TS'):
			return process_video_dvd(ipath + '/VIDEO_TS', output)
		print('Presumed DVD but could not find VOB files.')
		return 1
	
	titles = {}
	for fn in glob('%s/VTS_*.VOB' % ipath):
		sfn = fn.rsplit('/', 1)[-1]
		_, title, _ = sfn.split('_')
		if title not in titles:
			titles[title] = 0
		titles[title] += os.path.getsize(fn)
	biggest = max(titles, key=titles.get)
	ifo = read_video_ifo('%s/VTS_%s_0.IFO' % (ipath, biggest))
	pc = ifo.program_chains[0]
	vobs = sorted(glob('%s/VTS_%s_*.VOB' % (ipath, biggest)))
	assert len(vobs) > 0
	fsi = ffmpeg.probe(vobs[0])['streams']
	si = [x for x in fsi if 'channels' in x and x['channels'] > 2]
	if len(si) == 0:
		print('Could not find multichannel audio stream!')
		return 1
	si = sorted(si, key=lambda x: codec_priority.index(x['codec_name']) if x['codec_name'] in codec_priority else 10000)
	before = 0
	for elem in fsi:
		if elem is si[0]:
			break
		before += 1

	while True:
		artist_name = input('Artist name: ')
		album_name = input('Album name: ')
		if input('"%s" by %s. Is this correct? y/n: ' % (album_name, artist_name)) == 'y':
			break

	while True:
		print('Please enter the track names. If you wish to discard a track, simply hit enter.')
		track_names = []
		for i, pt in enumerate(pc.playback_times):
			name = input('Track %i (%s) name: ' % (i + 1, '%02i:%02i:%02i' % (pt.hours, pt.minutes, pt.seconds) if pt.hours != 0 else '%02i:%02i.%03i' % (pt.minutes, pt.seconds, int(pt.frames / 30 * 1000))))
			track_names.append(None if name == '' else name)
		print('Confirm:')
		for i, name in enumerate(track_names):
			if name is None:
				print('Track %i is DISCARDED' % (i + 1))
			else:
				print('Track %i is "%s"' % (i + 1, name))
		if input('Is this correct? y/n: ') == 'y':
			break
	
	fd, combined = tempfile.mkstemp(suffix='.vob')
	os.close(fd)
	with open(combined, 'wb') as ofp:
		for elem in vobs:
			with open(elem, 'rb') as ifp:
				ofp.write(ifp.read())
	
	start = 0
	procs = []
	for i, elem in enumerate(pc.playback_times):
		end = start + (elem.hours * 60 * 60 + elem.minutes * 60 + elem.seconds + elem.frames / 30)
		name = track_names[i]
		if name is None:
			start = end
			continue
		format_time = lambda x: '%02i:%02i:%02.3f' % (int(x / 60 / 60), int(x / 60 % 60), x % 60)
		args = ['ffmpeg', '-i', combined, '-ss', format_time(start), '-to', format_time(end), '-map', '0:%i' % before, '-map', '-0:v', '-metadata', 'title=%s' % name, '-metadata', 'artist=%s' % artist_name, '-metadata', 'album=%s' % album_name, '%s/%i - %s.flac' % (output, i + 1, name), '-y']
		print(args)
		procs.append(subprocess.Popen(args, stdout=subprocess.DEVNULL, stdin=subprocess.DEVNULL))
		start = end
	
	for i, elem in enumerate(procs):
		elem.wait()
		print('Process', i, 'completed')
	os.remove(combined)

def process_audio_dvd(ipath, output):
	if len(glob('%s/*.AOB' % ipath)) == 0:
		if os.path.exists(ipath + '/AUDIO_TS'):
			return process_audio_dvd(ipath + '/AUDIO_TS', output)
		print('Presumed DVD but could not find AOB files.')
		return 1

	titles = {}
	for fn in glob('%s/ATS_*.AOB' % ipath):
		sfn = fn.rsplit('/', 1)[-1]
		_, title, _ = sfn.split('_')
		if title not in titles:
			titles[title] = 0
		titles[title] += os.path.getsize(fn)
	biggest = max(titles, key=titles.get)
	track_ifo = read_audio_ifo('%s/ATS_%s_0.IFO' % (ipath, biggest))
	aobs = sorted(glob('%s/ATS_%s_*.AOB' % (ipath, biggest)))
	assert len(aobs) > 0
	fsi = ffmpeg.probe(aobs[0])['streams']
	si = [x for x in fsi if 'channels' in x and x['channels'] > 2]
	if len(si) == 0:
		print('Could not find multichannel audio stream!')
		return 1
	si = sorted(si, key=lambda x: codec_priority.index(x['codec_name']) if x['codec_name'] in codec_priority else 10000)
	before = 0
	for elem in fsi:
		if elem is si[0]:
			break
		before += 1

	while True:
		artist_name = input('Artist name: ')
		album_name = input('Album name: ')
		if input('"%s" by %s. Is this correct? y/n: ' % (album_name, artist_name)) == 'y':
			break

	format_time = lambda x: '%02i:%02i:%02.3f' % (int(x / 60 / 60), int(x / 60 % 60), x % 60) if x / 60 / 60 >= 1 else '%02i:%02.3f' % (int(x / 60), x % 60)

	while True:
		print('Please enter the track names. If you wish to discard a track, simply hit enter.')
		track_names = []
		for i, (_, length, _, _) in enumerate(track_ifo):
			name = input('Track %i (%s) name: ' % (i + 1, format_time(length)))
			track_names.append(None if name == '' else name)
		print('Confirm:')
		for i, name in enumerate(track_names):
			if name is None:
				print('Track %i is DISCARDED' % (i + 1))
			else:
				print('Track %i is "%s"' % (i + 1, name))
		if input('Is this correct? y/n: ') == 'y':
			break

	fd, combined = tempfile.mkstemp(suffix='.aob')
	os.close(fd)
	with open(combined, 'wb') as ofp:
		for elem in aobs:
			with open(elem, 'rb') as ifp:
				ofp.write(ifp.read())

	procs = []
	for i, (start, length, firstSector, lastSector) in enumerate(track_ifo):
		name = track_names[i]
		if name is None:
			continue
		args = ['ffmpeg', '-skip_initial_bytes', str(firstSector * 2048), '-i', combined, '-t', format_time(length), '-map', '0:%i' % before, '-map',
				'-0:v', '-metadata', 'title=%s' % name, '-metadata', 'artist=%s' % artist_name, '-metadata',
				'album=%s' % album_name, '%s/%i - %s.flac' % (output, i + 1, name), '-y']
		print(args)
		procs.append(subprocess.Popen(args, stdout=subprocess.DEVNULL, stdin=subprocess.DEVNULL))

	for i, elem in enumerate(procs):
		elem.wait()
		print('Process', i, 'completed')
	os.remove(combined)

def process_cue(ipath, output):
	cue = read_cue(ipath)
	ipath = os.path.dirname(os.path.abspath(ipath)) + '/' + cue.file
	artist_name = cue.performer
	album_name = cue.title

	format_time = lambda x: '%02i:%02i:%02i.%03i' % (x.days * 24 + int(x.seconds / 60 / 60), int(x.seconds / 60 % 60), int(x.seconds % 60), int(x.microseconds / 1000))

	procs = []
	for track in cue.tracks:
		offset = cueparser.offsetToTimedelta(track.offset)
		args = ['ffmpeg', '-i', ipath, '-ss', format_time(offset)]
		if track.duration is not None:
			args += ['-to', format_time(offset + track.duration)]
		args += ['-metadata', 'title=%s' % track.title, '-metadata', 'artist=%s' % artist_name, '-metadata', 'album=%s' % album_name, '%s/%i - %s.flac' % (output, track.number, track.title), '-y']
		print(args)
		procs.append(subprocess.Popen(args, stdout=subprocess.DEVNULL, stdin=subprocess.DEVNULL))
	for i, elem in enumerate(procs):
		elem.wait()
		print('Process', i, 'completed')

def process_sacd(ipath, output):
	dd = output + '/dsf'
	os.makedirs(dd, exist_ok=True)
	subprocess.run(['sacd_extract', '-w', '-m', '--output-dsf', '-i', ipath, '-o', dd])
	procs = []
	for elem in glob(dd + '/*/*.dsf'):
		args = ['ffmpeg', '-i', elem, '-c:a', 'flac', '-sample_fmt', 's32', '-ar', '96000', output + '/' + os.path.basename(elem).replace('dsf', 'flac')]
		procs.append(subprocess.Popen(args, stdout=subprocess.DEVNULL, stdin=subprocess.DEVNULL))
	for i, elem in enumerate(procs):
		elem.wait()
		print('Process', i, 'completed')
	shutil.rmtree(dd)

def process_mkv(ipath, output):
	print('Unimplemented')
	return 1

def main(ipath, output):
	if not os.path.exists(ipath):
		print('Invalid path', ipath)
		return 1
	
	os.makedirs(output, exist_ok=True)
	
	if os.path.isdir(ipath):
		if len(glob('%s/*.AOB' % ipath)) != 0 or os.path.isdir(ipath + '/AUDIO_TS'):
			return process_audio_dvd(ipath, output)
		return process_video_dvd(ipath, output)

	if ipath.endswith('.cue'):
		return process_cue(ipath, output)
	
	firstmeg = open(ipath, 'rb').read(1024 * 1024)
	if b'SACDMTOC' in firstmeg:
		return process_sacd(ipath, output)
	if b'matroska' in firstmeg:
		return process_mkv(ipath, output)
	print('Unknown file format')
	return 1

if __name__ == '__main__':
	sys.exit(main(*sys.argv[1:]))
