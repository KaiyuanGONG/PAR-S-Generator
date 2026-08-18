


              SIMIND Monte Carlo Simulation Program    V8.0  
------------------------------------------------------------------------------
 Phantom S : h2o       Crystal...: czt       InputFile.: attenuation_ict   
 Phantom B : h2o       BackScatt.: pmt       OutputFile: order_probe       
 Collimator: pb_sb2    SourceRout: smap      SourceImg.: water_column_mu_0p
 Cover.....: al        ScoreRout.: none      DensityImg: water_column_mu_0p
------------------------------------------------------------------------------
 PhotonEnergy.......: 140          Matrix    PhotonsPerProj....: 10             
 EnergyResolution...: 6.3          Spectra   Activity..........: 1704           
 MaxScatterOrder....: 1            ge-legp   DetectorLenght....: 25.585         
 DetectorWidth......: 19.68        SPECT     DetectorHeight....: 0.725          
 UpperEneWindowTresh: 154          BScatt    Distance to det...: 30             
 LowerEneWindowTresh: 126          Cover     ShiftSource X.....: 0              
 PixelSize  I.......: 0.442        Phantom   ShiftSource Y.....: 0              
 PixelSize  J.......: 0.442        Resolut   ShiftSource Z.....: 0              
 HalfLength S.......: 28.285       SaveMap   HalfLength P......: 28.285         
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
 Emission type......: 2                      Initial Weight....: *************  
 NN ScalingFactor...: 10                     Energy Channels...: 512            
                                                                              
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
 MatrixSize K.......: 128                    Units.............: g/cm3*1000          
 Scout File.........: F
                                                                              
 INTERACTIONS IN THE CRYSTAL
 MaxValue spectrum..: 0.4236E+05     
 MaxValue projection: 1670.          
 CountRate spectrum.: 0.3236E+05     
 CountRate E-Window.: 9079.          
                                                                              
 SCATTER IN ENERGY WINDOW
 Scatter/Primary....: 1.19759        
 Scatter/Total......: 0.54496        
 Scatter order 1....: 100    %       
                                                                              
 CALCULATED DETECTOR PARAMETERS
 Efficiency E-window: 0.2731         
 Efficiency spectrum: 0.9735         
 Sensitivity Cps/MBq: 5.3278         
 Sensitivity Cpm/uCi: 11.8277        
                                                                              
 Simulation started.: 2026:08:18 00:15:31
 Simulation stopped.: 2026:08:18 00:15:31
 Elapsed time.......: 0 h, 0 m and 0 s
 DetectorHits.......: 20             
 DetectorHits/CPUsec: 28             
                                                                              
 OTHER INFORMATION
 GE NM/CT 870 CZT simulation config ICT and analytic attenuation contra
 Compiled 2025:01:28 with INTEL Win   
 Current random number generator: ranmar
 Energy resolution as function of 1/sqrt(E)
 Linear angle sampling within acceptance angle
 Inifile: simind.ini
 Command: attenuation_ict order_probe /FS:water_column_mu_0p15 /FD:water_column_mu_0p15 /NN:10 /SC:1
