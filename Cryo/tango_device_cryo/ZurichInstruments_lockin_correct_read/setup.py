from setuptools import setup, find_packages

setup(
    name='tangods-ZI_DAQ',
    version='3.0.0',
    description='ZI MFLI Tango device (dev4855) with DAQ module averaging',
    packages=['ZI_DAQ'],
    entry_points={
        'console_scripts': [
            'ZI_DAQ = ZI_DAQ:main',
        ],
    },
    install_requires=[
        'pytango',
        'numpy',
        'zhinst',
    ],
)
