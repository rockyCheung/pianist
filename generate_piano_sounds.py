import numpy as np
from scipy.io import wavfile
from scipy.signal import butter, lfilter, firwin
from pydub import AudioSegment
from pydub.effects import normalize, compress_dynamic_range
import os
import numba as nb
from multiprocessing import Pool
import tempfile
import subprocess
import fluidsynth, wave
tempfile.tempdir = os.path.expanduser("~/tmp")  # 使用用户目录下的临时文件夹
os.makedirs(tempfile.tempdir, exist_ok=True)

# 创建输出目录
os.makedirs("sounds", exist_ok=True)
file_format = 'wav'  # 优先使用无损格式
# 音频参数
SAMPLE_RATE = 96000  # 提升采样率至96kHz
# 奈奎斯特频率
NYQUIST = SAMPLE_RATE / 2

BIT_DEPTH = 32  # 32-bit浮点处理
DURATION = 3.5  # 延长持续时间 音频持续时间毫秒

# 物理建模参数
HAMMER_HARDNESS = 0.9  # 琴槌硬度系数
STRING_LOSS = 1.2  # 琴弦能量损耗
ffmpeg_version = '4.0' #'6.1.1_2'

def check_ffmpeg_version():
    result = subprocess.run(['ffmpeg', '-version'], capture_output=True, text=True)
    print(result.stdout.split('\n')[0])  # 打印FFmpeg版本信息

@nb.njit(nb.float64[:](nb.float64[:], nb.float64, nb.int64), fastmath=True)
def generate_harmonics(t, frequency, midi_number):
    """类型明确的谐波生成函数"""
    harmonics = np.zeros_like(t)  # 明确使用float64类型

    fundamental_amp = 1.0 - (midi_number - 21) / 87 * 0.3
    harmonics = harmonics + fundamental_amp * np.sin(2 * np.pi * frequency * t)

    for n in range(2, 16):
        freq_mult = n + 0.2 * np.random.randn()
        amp = (1.0 / (n ** 1.2)) * (0.9 ** (midi_number / 12))
        amp *= 1.0 - 0.1 * (n % 3)
        # 使用显式赋值代替 +=
        harmonics = harmonics + amp * np.sin(2 * np.pi * frequency * freq_mult * t)

    # 非谐波成分使用独立数组
    inharmonic = np.zeros_like(t)
    for k in range(3):
        detune = 1 + 0.03 * (k + 1)
        inharmonic = inharmonic + 0.05 * np.sin(2 * np.pi * frequency * detune * t + np.pi / 4)

    return harmonics * 0.8 + inharmonic * 0.2


def physical_hammer_model(wave, midi_number):
    """琴槌物理特性模拟"""
    hardness = HAMMER_HARDNESS + (midi_number - 60) / 60 * 0.2
    wave = np.sign(wave) * np.abs(wave) ** (1 + hardness)
    return wave * 0.9 / np.max(np.abs(wave))

def butter_lowpass(cutoff, order=5):
    """设计巴特沃斯低通滤波器"""
    nyq = 0.5 * SAMPLE_RATE  # 奈奎斯特频率
    normal_cutoff = np.clip(cutoff / nyq, 0.001, 0.999)  # 确保截止频率有效
    b, a = butter(order, normal_cutoff, btype='low', analog=False)
    return b, a

def lowpass_filter(data, cutoff, order=5):
    """应用低通滤波器"""
    b, a = butter_lowpass(cutoff, order=order)
    y = lfilter(b, a, data)
    return y

def generate_piano_note(midi_number):
    # 计算基频
    frequency = 440 * (2 ** ((midi_number - 69 + np.random.uniform(-0.02, 0.02)) / 12))

    # 验证基频范围
    if frequency <= 0 or frequency >= NYQUIST:
        print(f"警告：MIDI {midi_number} 基频 {frequency:.1f}Hz 超出有效范围")
        frequency = np.clip(frequency, 20, NYQUIST * 0.99)

    # 高精度时间轴
    t = np.linspace(0, DURATION, int(SAMPLE_RATE * DURATION), dtype=np.float64)

    # 生成复合波形（包含动态谐波）
    wave = generate_harmonics(t, frequency, midi_number)

    # 琴槌物理建模
    wave = physical_hammer_model(wave, midi_number)

    # 高级ADSR包络
    attack = np.linspace(0, 1, int(SAMPLE_RATE * 0.005)) ** 3
    decay = np.exp(-np.linspace(0, 5, int(SAMPLE_RATE * 0.2)))
    release = np.exp(-np.linspace(0, 8, int(SAMPLE_RATE * 0.3)))

    envelope = np.concatenate((
        attack,
        decay * 0.7,
        np.linspace(0.7, 0.4, int(SAMPLE_RATE * (DURATION - 0.505))),
        release * 0.4
    ))[:len(t)]

    # 应用包络
    wave *= envelope

    # # 动态均衡处理
    # low_pass = firwin(101, [0.8 * frequency * 16, 1.2 * frequency * 16],
    #                   fs=SAMPLE_RATE, pass_zero=False)
    # wave = lfilter(low_pass, 1.0, wave)

    # 动态均衡处理
    if midi_number < 60:
        # 低音区增强（保持原有逻辑）
        cutoff = min(5000, NYQUIST * 0.99)
        wave = lowpass_filter(wave, cutoff)
        wave *= 1.2
    else:
        # 高音区高频滚降（修正FIR滤波器参数）
        base_freq = 440 * (2 ** ((midi_number - 69) / 12))
        cutoff = min(10000 - (midi_number - 60) * 100, NYQUIST * 0.99)
        wave = lowpass_filter(wave, cutoff)

    # 添加空间混响（立体声处理）
    left = wave * 0.9 + np.roll(wave, 500) * 0.1
    right = wave * 0.9 + np.roll(wave, 700) * 0.1
    stereo_wave = np.vstack((left, right)).T
    # 添加数据验证
    if np.any(np.isnan(stereo_wave)) or np.any(np.abs(stereo_wave) > 1.0):
        print(f"MIDI {midi_number} 数据异常，正在修正...")
        stereo_wave = np.nan_to_num(stereo_wave)
        stereo_wave = np.clip(stereo_wave, -0.99, 0.99)

    return stereo_wave.astype(np.float32)


def save_high_quality(wave, filename):
    """修复后的高音质保存函数"""
    try:
        # 使用临时WAV文件作为中介
        temp_wav = "temp_32bit.wav"
        wavfile.write(temp_wav, SAMPLE_RATE, wave)

        # 显式指定PCM格式
        audio = AudioSegment.from_wav(temp_wav)
        audio = audio.set_sample_width(3)  # 24-bit格式

        # 更新FLAC编码参数
        flac_params = [
            '-compression_level', '5',  # 修正参数格式
            '-lpc_type', 'levinson',  # 确保参数值合法
            '-exact_rice_parameters', '1'
        ]
        # 根据FFmpeg版本动态选择参数
        if ffmpeg_version >= '4.3':
            flac_params.append('-compression_level')
            flac_params.append('8')
        else:
            flac_params.append('-compression_level')
            flac_params.append('5')

        # 强制覆盖输出文件
        if os.path.exists(filename):
            os.remove(filename)

        audio.export(filename, format='flac', parameters=flac_params)

    except Exception as e:
        print(f"编码失败详细原因：{str(e)}")
        # 备用保存方案
        wavfile.write(filename.replace('.flac', '.wav'), SAMPLE_RATE, wave)
    finally:
        if os.path.exists(temp_wav):
            os.remove(temp_wav)


def midi_to_note_name(midi_number):
    notes = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
    octave = (midi_number // 12) - 1
    note_index = midi_number % 12
    return f"{notes[note_index]}{octave}"
# 并行生成函数
def generate_parallel(midi):
    try:
        note_name = midi_to_note_name(midi)
        print(f'note_name: {note_name}')
        filename = f"sounds/{note_name}.{file_format}"
        if os.path.exists(filename):
            return

        audio = generate_piano_note(midi)
        save_high_quality(audio, filename)
        print(f"生成成功：{note_name}.{file_format}")
    except Exception as e:
        print(f"生成失败（MIDI {midi}）：{str(e)}")


def generate_piano_note_with_soundfont():
    # 初始化FluidSynth
    fs = fluidsynth.Synth()
    fs.start()

    # 加载SoundFont文件（替换为你的路径）
    sfid = fs.sfload("sounds_source/FluidR3_GM.sf2")
    fs.program_select(0, sfid, 0, 0)  # 选择钢琴音色（0号程序）

    # 播放音符（A4，力度100，持续2秒）
    fs.noteon(0, 69, 100)  # MIDI编号69对应A4（440Hz）
    fluidsynth.wait(2)
    fs.noteoff(0, 69)

    # 生成音频数据并保存
    samples = fs.get_samples(int(2 * 44100))
    audio = np.frombuffer(samples, dtype=np.int16)

    # 保存为WAV文件
    with wave.open("sounds/real_piano_A4.wav", "wb") as wav_file:
        wav_file.setnchannels(2)  # FluidSynth输出立体声
        wav_file.setsampwidth(2)
        wav_file.setframerate(44100)
        wav_file.writeframes(audio.tobytes())

    fs.delete()




if __name__ == '__main__':
    # check_ffmpeg_version()
    # with Pool(processes=8) as pool:  # 使用8进程并行生成
    #     pool.map(generate_parallel, range(21, 109))
    # count = 0
    # for midi_number in range(21, 109):
    #     midi = midi_to_note_name(midi_number)
    #     count = count + 1
    #     print(f'midi_nate: {midi} ,number: {count}')

    generate_piano_note_with_soundfont()

    print("所有高音质音频生成完成！")