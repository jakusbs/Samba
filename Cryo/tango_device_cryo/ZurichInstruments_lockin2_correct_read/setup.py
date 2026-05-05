from setuptools import setup, find_packages

setup(
    name='tangods-ZI2_DAQ',
    version='3.0.0',
    description='ZI2 MFLI Tango device (dev30933) with DAQ module averaging',
    packages=['ZI2_DAQ'],
    entry_points={
        'console_scripts': [
            'ZI2_DAQ = ZI2_DAQ:main',
        ],
    },
    install_requires=[
        'pytango',
        'numpy',
        'zhinst',
    ],
)
