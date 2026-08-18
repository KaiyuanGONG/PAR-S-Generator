


              SIMIND Monte Carlo Simulation Program    V8.0  
------------------------------------------------------------------------------
 Phantom S : h2o       Crystal...: czt       InputFile.: attenuation_ict   
 Phantom B : h2o       BackScatt.: pmt       OutputFile: ladder_0p1500     
 Collimator: pb_sb2    SourceRout: smap      SourceImg.: input_0p1500_act_a
 Cover.....: al        ScoreRout.: scattwin  DensityImg: input_0p1500_atn_a
------------------------------------------------------------------------------
 PhotonEnergy.......: 140          Spectra   PhotonsPerProj....: 3000           
 EnergyResolution...: 6.3          ge-legp   Activity..........: 1704           
 MaxScatterOrder....: 3            SPECT     DetectorLenght....: 25.585         
 DetectorWidth......: 19.68        BScatt    DetectorHeight....: 0.725          
 UpperEneWindowTresh: 154          Cover     Distance to det...: 30             
 LowerEneWindowTresh: 126          Phantom   ShiftSource X.....: 0              
 PixelSize  I.......: 0.442        Resolut   ShiftSource Y.....: 0              
 PixelSize  J.......: 0.442        SaveMap   ShiftSource Z.....: 0              
 HalfLength S.......: 28.285                 HalfLength P......: 28.285         
 HalfWidth  S.......: 28.285                 HalfWidth  P......: 28.285         
 HalfHeight S.......: 28.285                 HalfHeight P......: 28.285         
 SourceType.........: XcatBinMap             PhantomType.......: XcatBinMap   
------------------------------------------------------------------------------
 GENERAL DATA
 keV/channel........: 0.5                    CutoffEnergy......: 0              
 Photons/Bq.........: 0.879                  StartingAngle.....: 180            
 CameraOffset X.....: 0                      CoverThickness....: 0.1            
 CameraOffset Y.....: 0                      BackscatterThickn.: 0.1            
 MatrixSize I.......: 128                    IntrinsicResolut..: 0              
 MatrixSize J.......: 128                    AcceptanceAngle...: 2.87511        
 Emission type......: 2                      Initial Weight....: 499272         
 NN ScalingFactor...: 3000                   Energy Channels...: 512            
                                                                              
 SOLID STATE DETECTOR SETTINGS 
 MobilLife electrons: 5                      MobilLife holes...: 0.4            
 Voltage anod/cathod: 600                    Contact pad size..: 0.16           
 Number detectors  I: 128                    Number Detectors J: 128            
 Anode element pitch: 0.246                  Tau decayConstant.: 0.4            
 EnergyResolut model: -2                     Hetch Model.......: 1              
 Flat detector shift: 1                      CloudMobility.....: 0.225          
                                                                              
 SPECT DATA
 RotationMode.......: 360                    Nr of Projections.: 60             
 RotationAngle......: 6                      Projection.[start]: 1              
 Orbital fraction...: 1                      Projection...[end]: 60             
                                                                              
 COLLIMATOR DATA FOR ROUTINE: Analytical          
 CollimatorCode.....: ge-legp                CollimatorType....: Parallel 
 HoleSize X.........: 0.226                  Distance X........: 0.02           
 HoleSize Y.........: 0.226                  Distance Y........: 0.02           
 CenterShift X......: 0                      X-Ray flag........: F              
 CenterShift Y......: 0                      CollimThickness...: 4.5            
 HoleShape..........: Rectangular            Space Coll2Det....: 0              
 CollDepValue [57]..: 0                      CollDepValue [58].: 0              
 CollDepValue [59]..: 0                      CollDepValue [60].: 0              
                                                                              
 IMAGE-BASED PHANTOM DATA
 RotationCentre.....:  65, 65                Bone definition...: 1170           
 CT-Pixel size......: 0.442                  Slice thickness...: 0.44195        
 StartImage.........: 1                      No of CT-Images...: 128            
 MatrixSize I.......: 128                    CTmapOrientation..: 0              
 MatrixSize J.......: 128                    StepSize..........: 0.1            
 CenterPoint I......: 65                     ShiftPhantom X....: 0              
 CenterPoint J......: 65                     ShiftPhantom Y....: 0              
 CenterPoint K......: 65                     ShiftPhantom Z....: 0              
                                                                              
 INFO FOR TCT file
 MatrixSize I.......: 128                    MatrixSize J......: 128            
 MatrixSize K.......: 128                    Units.............: mu                  
 Scout File.........: F
------------------------------------------------------------------------------
  Scattwin results: Window file: attenuation_ict.win 
  
  Win  WinAdded  Range(keV)   ScaleFactor
   1       0    126.0 - 154.0   1.000
  
  Win    Total    Scatter   Primary  S/P-Ratio S/T Ratio  Cps/MBq
   1   0.539E+06 0.274E+06 0.265E+06 0.103E+01 0.508E+00 0.527E+01
  
  Win  Geo(Air)  Pen(Air)  Sca(Air)  Geo(Tot)  Pen(Tot)  Sca(Tot)
   1   100.00%     0.00%     0.00%   100.00%     0.00%     0.00%
  
  Win   SC 1  SC 2  SC 3
   1   75.3% 21.7%  3.0%
                                                                              
 INTERACTIONS IN THE CRYSTAL
 MaxValue spectrum..: 0.2761E+05     
 MaxValue projection: 305.4          
 CountRate spectrum.: 0.7434E+05     
 CountRate E-Window.: 8981.          
                                                                              
 SCATTER IN ENERGY WINDOW
 Scatter/Primary....: 1.03288        
 Scatter/Total......: 0.50809        
 Scatter order 1....: 75.32 %        
 Scatter order 2....: 21.67 %        
 Scatter order 3....: 3.01 %         
                                                                              
 CALCULATED DETECTOR PARAMETERS
 Efficiency E-window: 0.1186         
 Efficiency spectrum: 0.9821         
 Sensitivity Cps/MBq: 5.2704         
 Sensitivity Cpm/uCi: 11.7002        
                                                                              
 Simulation started.: 2026:08:18 10:07:39
 Simulation stopped.: 2026:08:18 10:07:44
 Elapsed time.......: 0 h, 0 m and 5 s
 DetectorHits.......: 11029          
 DetectorHits/CPUsec: 2107           
                                                                              
 OTHER INFORMATION
 GE NM/CT 870 CZT simulation config paired type-7 mu-times-voxel analyt
 Compiled 2025:01:28 with INTEL Win   
 Current random number generator: ranmar
 Energy resolution as function of 1/sqrt(E)
 Linear angle sampling within acceptance angle
 Inifile: simind.ini
 Command: attenuation_ict ladder_0p1500 /FS:input_0p1500 /FD:input_0p1500 /NN:3000 /IN:x22,3x/84:1/CA:2
